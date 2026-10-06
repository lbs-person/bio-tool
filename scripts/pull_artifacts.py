# scripts/pull_artifacts.py
"""
从 GitHub Actions 下载抓图产物（artifact），合并回本地 output/。

背景：图片不进 git 仓库，抓图结果以 artifact 形式存在 Actions 上。
Actions 不会把结果推回仓库，所以本地要拿到数据必须主动下载。

三处必须小心的地方（都来自真实的 artifact 列表）：

1. 旧的 `shard-N` artifact（约 270 MB）是更早的 20 分片方案遗留，创建时间
   最早。它里面的元数据是旧的 500 行格式，覆盖它会把新成果打回原形，所以
   一律跳过。

2. 失败运行留下的 artifact 名字也是 `shard-N-<run>-<attempt>`，但只有
   100 KB 左右（job 挂在装依赖那一步，output/ 几乎是空的）。靠大小过滤掉。

3. 同分片的多个 artifact 内容互相重叠。解压顺序决定谁赢，所以：
   - 图片只增不减，重复覆盖无副作用；
   - 元数据 CSV 按「新的赢」处理，绝不让旧数据覆盖新数据。

需要的权限：repo。私有仓库读 artifact 必须有它，公开仓库的 artifact
下载接口也不给匿名访问。

Token 获取：https://github.com/settings/tokens
  - 经典 token：勾选 repo 即可
  - 细粒度 token：仓库权限里给 Actions = Read

用法：
    set GITHUB_TOKEN=ghp_xxx
    python scripts/pull_artifacts.py --dry-run    # 先看会下载什么
    python scripts/pull_artifacts.py
"""
import os
import re
import sys
import csv
import glob
import shutil
import zipfile
import argparse
import tempfile

import requests

# 本机网络存在中间人代理（TLS 重签），Python 自带的 CA 包验不过证书：
#   SSLCertVerificationError: unable to get local issuer certificate
# 而 git / 浏览器正常，因为它们用的是 Windows 证书存储。
# truststore 让 Python 也走系统证书存储，从而与 git 保持一致。
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
OUT_DIR = os.path.join(ROOT, "output")

API = "https://api.github.com"
DEFAULT_REPO = "lbs-person/bio-tool"

# 新版 artifact 名：shard-<分片>-<run_id>-<attempt>
NEW_NAME_RE = re.compile(r"^shard-(\d+)-(\d+)-(\d+)$")
# 旧版遗留名：shard-<分片>
OLD_NAME_RE = re.compile(r"^shard-\d+$")

# 低于这个大小的 artifact 视为失败运行的空产物，跳过
MIN_BYTES = 1024 * 1024


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def is_meta_csv(name):
    return name.startswith("images_meta") and name.endswith(".csv") and "/" not in name


def gh(session, url, **kw):
    r = session.get(url, timeout=60, **kw)
    if r.status_code == 401:
        raise SystemExit(
            "认证失败（401）。请检查 token 是否有效、是否勾了 repo 权限。\n"
            "  创建地址: https://github.com/settings/tokens")
    if r.status_code == 404:
        raise SystemExit(
            f"找不到：{url}\n"
            "  常见原因：仓库名写错，或 token 没有该仓库的访问权限。")
    if r.status_code != 200:
        raise SystemExit(f"请求失败 {r.status_code}: {url}\n{r.text[:300]}")
    return r


def list_artifacts(session, repo, shard=None):
    arts, page = [], 1
    while True:
        r = gh(session, f"{API}/repos/{repo}/actions/artifacts",
               params={"per_page": 100, "page": page})
        data = r.json()
        arts.extend(data.get("artifacts", []))
        if len(arts) >= data.get("total_count", 0) or not data.get("artifacts"):
            break
        page += 1

    kept, skipped = [], []
    for a in arts:
        if a.get("expired"):
            skipped.append((a, "已过期"))
            continue
        name = a["name"]
        if OLD_NAME_RE.match(name):
            skipped.append((a, "旧方案遗留，会覆盖新数据"))
            continue
        m = NEW_NAME_RE.match(name)
        if not m:
            skipped.append((a, "名字不符合命名规则"))
            continue
        if shard is not None and int(m.group(1)) != shard:
            continue
        if a.get("size_in_bytes", 0) < MIN_BYTES:
            skipped.append((a, f"只有 {human(a.get('size_in_bytes', 0))}，疑似失败运行"))
            continue
        a["_shard"] = int(m.group(1))
        kept.append(a)
    return kept, skipped


def download(session, repo, artifact, dest_zip):
    url = f"{API}/repos/{repo}/actions/artifacts/{artifact['id']}/zip"
    with session.get(url, stream=True, timeout=300, allow_redirects=True) as r:
        if r.status_code != 200:
            raise SystemExit(f"下载失败 {r.status_code}: {artifact['name']}")
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(dest_zip, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                f.write(chunk)
                got += len(chunk)
                if total:
                    sys.stdout.write(
                        f"\r    {human(got)} / {human(total)} ({100.0 * got / total:5.1f}%)")
                    sys.stdout.flush()
        sys.stdout.write(f"\r    下载完成 {human(got)}          \n")
    return got


def csv_row_count(path):
    """数一个 CSV 的数据行数，用于判断该保留哪一份元数据"""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)
    except OSError:
        return -1


def extract_into(zip_path, out_dir, keep_meta=False):
    """
    把 artifact 解压进 output/。

    keep_meta=True 时，同名的元数据 CSV 不按「存在与否」判断，而是比行数：
    本地已有的行数更多就保留本地，否则用 artifact 里的覆盖。

    为什么必须比行数：最初写成「已存在就跳过」，结果 sh04 本地是 500 行的
    旧文件、artifact 里是 14,802 行的新文件，新数据被整份丢掉，而对应的图片
    已经解压到磁盘上，于是变成「有图无元数据」的孤儿图。
    """
    files = 0
    kept_local = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # 有些打包方式会带 output/ 前缀，去掉以免变成 output/output/
            if name.startswith("output/"):
                name = name[len("output/"):]
            if not name or name.endswith("/"):
                continue

            target = os.path.join(out_dir, name.replace("/", os.sep))

            if keep_meta and is_meta_csv(name) and os.path.exists(target):
                with z.open(info) as src:
                    data = src.read()
                new_rows = max(0, data.decode("utf-8-sig", "replace").count("\n") - 1)
                if csv_row_count(target) >= new_rows:
                    kept_local += 1
                    continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files += 1
    return files, kept_local


def main():
    ap = argparse.ArgumentParser(
        description="从 GitHub Actions 下载抓图产物并合并回 output/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"默认 {DEFAULT_REPO}")
    ap.add_argument("--shard", type=int, default=None, help="只拉这个分片")
    ap.add_argument("--token", default=None, help="GitHub token，默认读 GITHUB_TOKEN")
    ap.add_argument("--dry-run", action="store_true", help="只列出会下载什么")
    args = ap.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit(
            "没有 token。请设置环境变量 GITHUB_TOKEN，或用 --token 传入。\n"
            "  创建: https://github.com/settings/tokens（经典 token 勾 repo）\n"
            "  PowerShell:  $env:GITHUB_TOKEN='ghp_xxx'")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bio-tool-pull-artifacts",
    })

    print(f"仓库: {args.repo}\n")
    kept, skipped = list_artifacts(session, args.repo, args.shard)

    if skipped:
        print(f"跳过 {len(skipped)} 个 artifact：")
        for a, why in skipped[:12]:
            print(f"  {a['name']:<42} {human(a.get('size_in_bytes', 0)):>10}  {why}")
        if len(skipped) > 12:
            print(f"  ...（另有 {len(skipped) - 12} 个）")
        print()

    if not kept:
        scope = f"分片 {args.shard}" if args.shard is not None else "任意分片"
        print(f"没有可用的 artifact（{scope}）。")
        return 1

    # 新的先解压：元数据以最新为准，后续 artifact 不再覆盖同名 CSV
    kept.sort(key=lambda a: a.get("created_at", ""), reverse=True)

    total_size = sum(a.get("size_in_bytes", 0) for a in kept)
    print(f"将下载 {len(kept)} 个 artifact，合计约 {human(total_size)}：")
    for a in kept:
        print(f"  {a['name']:<42} {human(a.get('size_in_bytes', 0)):>10}"
              f"  {a.get('created_at', '')[:19]}")
    print()

    if args.dry_run:
        print("--dry-run：没有下载任何东西")
        return 0

    img_glob = os.path.join(OUT_DIR, "images", "**", "*.webp")
    before = len(glob.glob(img_glob, recursive=True))
    os.makedirs(OUT_DIR, exist_ok=True)

    total_files = total_kept_local = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, a in enumerate(kept):
            zip_path = os.path.join(tmp, a["name"] + ".zip")
            print(f"下载 {a['name']} ...")
            download(session, args.repo, a, zip_path)
            n, sk = extract_into(zip_path, OUT_DIR, keep_meta=True)
            total_files += n
            total_kept_local += sk
            msg = f"    解压 {n} 个文件"
            if sk:
                msg += f"，另有 {sk} 个元数据 CSV 本地行数更多故保留"
            print(msg)
            os.remove(zip_path)

    after = len(glob.glob(img_glob, recursive=True))
    print(f"\n完成：解压 {total_files} 个文件，保留本地版本 {total_kept_local} 个元数据 CSV")
    print(f"图片数量 {before} -> {after}")

    print("\n下一步（务必执行，它防的正是历史上出过的事故）：")
    print("  python scripts/verify_meta.py")
    print("  python scripts/merge_meta.py")
    print("  python scripts/build_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
