# scripts/verify_meta.py
"""
校验图片与元数据的一一对应关系，并把历史命名/表结构的元数据迁移到当前格式。

这个脚本存在的理由：项目曾经出现过 3,579 张图片里只有 626 张有元数据记录、
2,953 张成为无法追溯来源与授权的孤儿图。此后每次抓图收尾都必须跑一次校验。

用法：
    python scripts/verify_meta.py              # 校验全部元数据
    python scripts/verify_meta.py --migrate    # 先把旧格式迁移到新格式，再校验
    python scripts/verify_meta.py --fix        # 删除无元数据的孤儿图片
"""
import os
import csv
import glob
import argparse
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
OUT_DIR = os.path.join(ROOT, "output")
IMAGES_DIR = os.path.join(OUT_DIR, "images")

SCHEMA_VERSION = "2"

# 当前分片文件名：images_meta_sh00_of10.csv
NEW_RE = re.compile(r"^images_meta_sh(\d+)_of(\d+)\.csv$")
# 历史命名：images_meta_00.csv
OLD_RE = re.compile(r"^images_meta_(\d+)\.csv$")

FIELDS = [
    "taxon_id", "sci_name", "cn_name",
    "matched_name", "match_type",
    "image_path", "image_url", "image_bytes", "image_w", "image_h",
    "author", "license", "license_url", "page_url",
    "inat_taxon_id", "status", "schema_version",
]


def read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def migrate(verbose=True):
    """
    把 images_meta_00.csv 这类历史文件改名为 images_meta_sh00_of10.csv，
    并把缺失的列补空、scheme 版本更新。历史分片用的是 10 分片方案。
    """
    legacy = sorted(glob.glob(os.path.join(OUT_DIR, "images_meta_*.csv")))
    moved = 0
    for path in legacy:
        name = os.path.basename(path)
        if NEW_RE.match(name) or name == "images_meta.csv":
            continue
        m = OLD_RE.match(name)
        if not m:
            continue
        shard = int(m.group(1))
        target = os.path.join(OUT_DIR, f"images_meta_sh{shard:02d}_of10.csv")

        rows = read_rows(path)
        for row in rows:
            for col in FIELDS:
                row.setdefault(col, "")
            if not row.get("schema_version"):
                # 历史行没有 schema_version，标记为 1 便于区分
                row["schema_version"] = "1"
            for col in FIELDS:
                if row.get(col) is None:
                    row[col] = ""

        with open(target, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.remove(path)
        moved += 1
        if verbose:
            print(f"迁移 {name} -> {os.path.basename(target)}（{len(rows)} 行）")

    if moved and verbose:
        print(f"共迁移 {moved} 个历史元数据文件")

    # 旧的合并结果已经不是当前格式，避免误导后续脚本
    merged = os.path.join(OUT_DIR, "images_meta.csv")
    if os.path.exists(merged):
        os.remove(merged)
        if verbose:
            print("删除过期的 output/images_meta.csv，将由 merge_meta.py 重新生成")
    return moved


def collect():
    """汇总所有当前格式的元数据行"""
    rows = []
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "images_meta_sh*_of*.csv"))):
        rows.extend(read_rows(path))
    return rows


def audit(fix=False):
    rows = collect()
    if not rows:
        print("没有找到任何元数据文件（output/images_meta_sh*_of*.csv）")
        return 1

    ok_rows = [r for r in rows if (r.get("status") or "") == "ok"]
    ok_with_path = [r for r in ok_rows if (r.get("image_path") or "").strip()]

    missing = []
    for r in ok_with_path:
        p = os.path.join(OUT_DIR, r["image_path"].replace("/", os.sep))
        if not os.path.exists(p):
            missing.append(r)

    no_license = [r for r in ok_with_path
                  if not (r.get("license") or "").strip()
                  or not (r.get("author") or "").strip()]

    referenced = {(r.get("image_path") or "").strip()
                  for r in ok_with_path if (r.get("image_path") or "").strip()}
    on_disk = []
    for dp, _, fns in os.walk(IMAGES_DIR):
        for fn in fns:
            on_disk.append(os.path.relpath(os.path.join(dp, fn), OUT_DIR).replace("\\", "/"))
    orphan = sorted(set(on_disk) - referenced)

    print("=" * 60)
    print(f"元数据总记录     : {len(rows)}")
    print(f"  status=ok      : {len(ok_rows)}")
    print(f"  其中带路径     : {len(ok_with_path)}")
    print(f"  其他状态       : {len(rows) - len(ok_rows)}")
    print(f"磁盘图片总数     : {len(on_disk)}")
    print("=" * 60)

    problems = 0

    if missing:
        problems += 1
        print(f"[错误] {len(missing)} 条 ok 记录指向的图片不存在：")
        for r in missing[:10]:
            print(f"    {r.get('sci_name','')} -> {r.get('image_path','')}")
    else:
        print("[通过] 所有 ok 记录都能在磁盘上找到对应图片")

    if orphan:
        problems += 1
        print(f"[错误] {len(orphan)} 张图片没有任何元数据记录（无法追溯来源与授权）：")
        for p in orphan[:10]:
            print(f"    {p}")
        if fix:
            for p in orphan:
                fp = os.path.join(OUT_DIR, p.replace("/", os.sep))
                try:
                    os.remove(fp)
                except OSError as exc:
                    print(f"    删除失败 {p}: {exc}")
            print(f"    已删除 {len(orphan)} 张孤儿图片")
    else:
        print("[通过] 磁盘上没有无元数据的孤儿图片")

    if no_license:
        problems += 1
        print(f"[错误] {len(no_license)} 条 ok 记录缺少作者或许可证信息（CC BY 系列要求署名）：")
        for r in no_license[:10]:
            print(f"    {r.get('sci_name','')} license={r.get('license','')!r}")
    else:
        print("[通过] 所有 ok 记录都带作者与许可证信息")

    print("=" * 60)
    if problems:
        print(f"发现 {problems} 类问题")
        return 1
    print("校验通过：图片与元数据一一对应，授权信息完整")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--migrate", action="store_true", help="先把历史格式迁移到当前格式")
    ap.add_argument("--fix", action="store_true", help="删除无元数据的孤儿图片")
    args = ap.parse_args()

    if args.migrate:
        migrate()
        print()
    raise SystemExit(audit(fix=args.fix))


if __name__ == "__main__":
    main()
