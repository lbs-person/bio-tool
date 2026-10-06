# scripts/fetch_images.py
"""
从 iNaturalist 抓取物种图片与版权元数据，跑在 GitHub Actions 上。

设计要点（针对历史上出现过的元数据丢失问题）：

1. 元数据必须跟得上图片。只有图片原子写入成功、且 PIL 能重新打开校验通过，
   才会写入 status=ok 的元数据行。绝不先落图后记录。
2. 图片与元数据必须一一对应。启动时校验已有元数据，status=ok 但磁盘上
   文件缺失的记录会被重新抓取。
3. 分片文件名固化分片数。文件名形如 images_meta_sh010_of10.csv，改动
   --shard-total 不会与历史文件互相覆盖。
4. 按时间预算退出。--max-minutes 到点就正常收尾退出，配合 artifact 上传，
   避免 workflow 超时导致整批结果丢失。

用法：
    python scripts/fetch_images.py --shard 3 --shard-total 10 --count 2000
    python scripts/fetch_images.py --shard 3 --shard-total 10 --verify-only
"""
import os
import csv
import time
import argparse
from io import BytesIO

import pandas as pd
import requests
from PIL import Image
from tqdm import tqdm

# 部分网络环境下存在中间人代理（TLS 重签），Python 自带的 CA 包验不过证书，
# 会抛 SSLCertVerificationError: unable to get local issuer certificate。
# 这类错误会被下面的 except 吞掉，表现为物种莫名其妙变成 no_photo / no_taxon，
# 很难察觉。truststore 让 Python 改用 Windows 证书存储，与 git / 浏览器一致。
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

SPECIES_CSV = os.path.join(ROOT, "data", "species_for_images.csv")
OUT_DIR = os.path.join(ROOT, "output")
IMAGES_DIR = os.path.join(OUT_DIR, "images")

USER_AGENT = "BioOfflineImageBot/1.0 (https://github.com/lbs-person/bio-tool)"
MAX_SIZE = 800
WEBP_QUALITY = 80
ALLOWED = {"cc0", "cc-by", "cc-by-sa"}

# iNaturalist 未认证请求约 100 次/分钟；取 0.6s 作为保守间隔
MIN_INTERVAL = 0.6
_last_request = [0.0]

FIELDS = [
    "taxon_id", "sci_name", "cn_name",
    "matched_name", "match_type",
    "image_path", "image_url", "image_bytes", "image_w", "image_h",
    "author", "license", "license_url", "page_url",
    "inat_taxon_id", "status", "schema_version",
]
SCHEMA_VERSION = "2"


def throttle(sleep):
    """全局请求限速，保证任意两次请求之间至少间隔 sleep 秒"""
    gap = time.time() - _last_request[0]
    if gap < sleep:
        time.sleep(sleep - gap)
    _last_request[0] = time.time()


def meta_path(shard, shard_total):
    return os.path.join(OUT_DIR, f"images_meta_sh{shard:02d}_of{shard_total:02d}.csv")


def shard_path(taxon_id):
    s = str(taxon_id).zfill(8)
    return f"images/{s[0:2]}/{s[2:4]}/{s}.webp"


def load_existing(csv_path):
    """读回已有元数据，返回 (已成功且文件仍在的学名集合, 全部记录)"""
    if not os.path.exists(csv_path):
        return set(), []
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    good = set()
    for row in rows:
        rel = (row.get("image_path") or "").strip()
        if (row.get("status") or "") == "ok" and rel:
            if os.path.exists(os.path.join(OUT_DIR, rel.replace("/", os.sep))):
                good.add(row["sci_name"])
    return good, rows


def verify(csv_path):
    """
    校验本分片：每条 status=ok 记录都指向存在的图片，
    且该分片应有的图片文件里没有在元数据中缺席的。
    其它分片的图片不参与判定（用 verify_meta.py 做全库校验）。

    返回 0 表示通过。
    """
    if not os.path.exists(csv_path):
        print(f"没有元数据文件: {csv_path}")
        return 1
    _, rows = load_existing(csv_path)
    if not rows:
        print("元数据为空")
        return 1

    problems = 0

    # 正向：ok 记录必须能找到文件
    ok_rows = [r for r in rows if (r.get("status") or "") == "ok"
               and (r.get("image_path") or "").strip()]
    missing = [r["image_path"] for r in ok_rows
               if not os.path.exists(os.path.join(OUT_DIR, r["image_path"].replace("/", os.sep)))]
    if missing:
        problems += 1
        print(f"[错误] {len(missing)} 条 ok 记录指向的图片不存在：")
        for p in missing[:5]:
            print(f"    {p}")
    else:
        print(f"[通过] {len(ok_rows)} 条 ok 记录都能找到图片")

    # 反向：本分片物种对应的图片文件，必须在元数据里出现过
    referenced = {(r.get("image_path") or "").strip() for r in rows}
    referenced.discard("")
    orphan = []
    for r in rows:
        try:
            tid = int(r["taxon_id"])
        except (KeyError, TypeError, ValueError):
            continue
        rel = shard_path(tid)
        if os.path.exists(os.path.join(OUT_DIR, rel.replace("/", os.sep))) and rel not in referenced:
            orphan.append(rel)

    if orphan:
        problems += 1
        print(f"[错误] {len(orphan)} 张本分片图片没有元数据记录：")
        for p in orphan[:5]:
            print(f"    {p}")
    else:
        print("[通过] 本分片没有无元数据的图片")

    print(f"记录 {len(rows)} 行，其中 ok {len(ok_rows)} 行")
    return 1 if problems else 0


def inat_taxon(sci_name, sleep):
    """
    用学名查 iNaturalist taxon。
    返回 (taxon_id, matched_name, match_type)，match_type 为 exact / synonym / none
    """
    throttle(sleep)
    try:
        r = requests.get(
            "https://api.inaturalist.org/v1/taxa",
            params={"q": sci_name, "rank": "species,subspecies", "per_page": 5},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            return None, "", "none"
        results = r.json().get("results", [])
    except Exception:
        return None, "", "none"

    if not results:
        return None, "", "none"

    target = sci_name.strip().lower()
    for res in results:
        if (res.get("name") or "").strip().lower() == target:
            return res["id"], res.get("name", ""), "exact"

    # 学名可能已并入别的种（异名），iNat 会返回接受名
    top = results[0]
    return top["id"], top.get("name", ""), "synonym"


def inat_photo(taxon_id, sleep):
    """取该 taxon 排名最高的合规授权照片，返回 dict 或 None"""
    throttle(sleep)
    try:
        r = requests.get(
            "https://api.inaturalist.org/v1/observations",
            params={
                "taxon_id": taxon_id,
                "photos": "true",
                "per_page": 10,
                "order_by": "votes",
                "order": "desc",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    for obs in data.get("results", []):
        for photo in obs.get("photos", []):
            lic = (photo.get("license_code") or "").lower()
            if lic not in ALLOWED:
                continue
            url = photo.get("url") or ""
            if not url:
                continue
            return {
                "url": url.replace("/square.", "/large."),
                "author": photo.get("attribution") or "",
                "license": lic,
                "license_url": f"https://creativecommons.org/licenses/{lic}/4.0/",
                "page_url": f"https://www.inaturalist.org/observations/{obs.get('id')}",
            }
    return None


def download_image(url, out_path, sleep):
    """
    下载 → 校验 → 缩放 → 原子写入。
    返回 (成功, 字节数, 宽, 高)。任何一步失败都返回 (False, 0, 0, 0)，不留半成品文件。
    """
    throttle(sleep)
    tmp = out_path + ".tmp"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        if r.status_code != 200:
            return False, 0, 0, 0
        img = Image.open(BytesIO(r.content))
        img.load()
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_SIZE:
            if w >= h:
                img = img.resize((MAX_SIZE, max(1, int(h * MAX_SIZE / w))),
                                 Image.Resampling.LANCZOS)
            else:
                img = img.resize((max(1, int(w * MAX_SIZE / h)), MAX_SIZE),
                                 Image.Resampling.LANCZOS)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(tmp, "WEBP", quality=WEBP_QUALITY, method=6)
        # 重新打开确认文件真的可读，避免留下损坏的 webp
        with Image.open(tmp) as check:
            check.load()
            fw, fh = check.size
        os.replace(tmp, out_path)
        return True, os.path.getsize(out_path), fw, fh
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, 0, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shard-total", type=int, default=10)
    ap.add_argument("--count", type=int, default=2000, help="本次最多处理多少个物种")
    ap.add_argument("--sleep", type=float, default=MIN_INTERVAL, help="两次请求最小间隔（秒）")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="运行时间上限，到点正常收尾退出；0 表示不限制")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个物种，用于冒烟测试")
    ap.add_argument("--verify-only", action="store_true", help="只校验元数据与图片对应关系")
    args = ap.parse_args()

    if not 0 <= args.shard < args.shard_total:
        raise SystemExit(f"shard 必须在 0..{args.shard_total - 1} 之间")

    csv_path = meta_path(args.shard, args.shard_total)

    if args.verify_only:
        raise SystemExit(verify(csv_path))

    os.makedirs(IMAGES_DIR, exist_ok=True)

    species = pd.read_csv(SPECIES_CSV, dtype=str).fillna("").to_dict("records")
    shard_species = [sp for i, sp in enumerate(species)
                     if i % args.shard_total == args.shard]

    done, existing_rows = load_existing(csv_path)

    # 已记录过但图片已不在的记录（不该出现，出现即说明元数据与图片脱节）
    stale = [r for r in existing_rows
             if r.get("status") == "ok"
             and (r.get("image_path") or "").strip()
             and not os.path.exists(
                 os.path.join(OUT_DIR, r["image_path"].replace("/", os.sep)))]
    if stale:
        print(f"警告：{len(stale)} 条 ok 记录的图片文件缺失，将重新抓取")

    todo = [sp for sp in shard_species
            if sp["sci_name_for_image"].strip() not in done]
    if args.limit:
        todo = todo[:args.limit]
    todo = todo[:args.count]

    print(f"分片 {args.shard}/{args.shard_total}：本片 {len(shard_species)} 个物种，"
          f"已完成 {len(done)}，本次计划 {len(todo)}")

    if not todo:
        print("没有待处理物种")
        verify(csv_path)
        return

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None

    # 追加模式，保留已有行
    file_exists = os.path.exists(csv_path)
    mode = "a" if file_exists else "w"
    mf = open(csv_path, mode, encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(mf, fieldnames=FIELDS, extrasaction="ignore")
    if mode == "w":
        writer.writeheader()
        mf.flush()

    stats = {"ok": 0, "no_photo": 0, "no_taxon": 0, "download_failed": 0}
    processed = 0

    for sp in tqdm(todo, desc=f"shard {args.shard}"):
        if deadline and time.time() > deadline:
            print(f"已达时间预算（{args.max_minutes} 分钟），正常收尾")
            break

        sci = sp["sci_name_for_image"].strip()
        cn = sp["cn_name"].strip()
        tid = int(sp["taxon_id"])

        taxon_id, matched, mtype = inat_taxon(sci, args.sleep)

        # 学名带亚种时退一步用二名法再试一次
        if not taxon_id and len(sci.split()) > 2:
            binom = " ".join(sci.split()[:2])
            taxon_id, matched, mtype = inat_taxon(binom, args.sleep)

        if not taxon_id:
            stats["no_taxon"] += 1
            writer.writerow({"taxon_id": tid, "sci_name": sci, "cn_name": cn,
                             "status": "no_taxon", "schema_version": SCHEMA_VERSION})
            mf.flush()
            processed += 1
            continue

        info = inat_photo(taxon_id, args.sleep)
        if not info:
            stats["no_photo"] += 1
            writer.writerow({"taxon_id": tid, "sci_name": sci, "cn_name": cn,
                             "matched_name": matched, "match_type": mtype,
                             "inat_taxon_id": taxon_id,
                             "status": "no_photo", "schema_version": SCHEMA_VERSION})
            mf.flush()
            processed += 1
            continue

        rel = shard_path(tid)
        ok, nbytes, iw, ih = download_image(
            info["url"], os.path.join(OUT_DIR, rel.replace("/", os.sep)), args.sleep)

        if ok:
            stats["ok"] += 1
            status = "ok"
        else:
            stats["download_failed"] += 1
            status = "download_failed"
            rel = ""

        # 图片已经落盘校验通过，才写下 ok 行
        writer.writerow({
            "taxon_id": tid, "sci_name": sci, "cn_name": cn,
            "matched_name": matched, "match_type": mtype,
            "image_path": rel, "image_url": info["url"],
            "image_bytes": nbytes, "image_w": iw, "image_h": ih,
            "author": info["author"], "license": info["license"],
            "license_url": info["license_url"], "page_url": info["page_url"],
            "inat_taxon_id": taxon_id, "status": status,
            "schema_version": SCHEMA_VERSION,
        })
        mf.flush()
        os.fsync(mf.fileno())
        processed += 1

    mf.close()

    print(f"分片 {args.shard} 处理 {processed} 个，成功 {stats['ok']} 张")
    print(f"明细: {stats}")

    rc = verify(csv_path)
    if rc == 0:
        print("校验通过：本分片元数据与图片一一对应")
    else:
        print("校验未通过，请检查上面的缺失项")
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
