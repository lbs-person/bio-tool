# build_assets.py
"""
为 Android APK 打包准备资产。

产出 dist_assets/：
  bio.db        SQLite 数据库（供 sql.js 在 WebView 里查询）
  images/       压缩后的图片，路径与 bio.db 的 image_path 对齐
  manifest.json 版本与统计信息

两个关键设计决定：

1. 用字典表存分类阶元。156,107 行里 kingdom_cn / phylum_cn / class_cn …
   这些字符串高度重复（界只有几个、属也就几千个），直接存在主表里会把
   库撑大好几倍。拆成 tax 字典表后主表只存整数 id。

2. 不存 image_url。原始图片地址是 https://inaturalist-open-data.s3...
   这种几十字节的长 URL，对离线 App 毫无用处（离线就是要看本地图）。
   溯源真正需要的是 page_url（观察记录页），保留它。

用法：
    python scripts/build_assets.py
    python scripts/build_assets.py --size 384 --quality 70
    python scripts/build_assets.py --limit 100          # 快速验证
    python scripts/build_assets.py --db-only            # 只重建数据库
"""
import os
import sys
import json
import shutil
import sqlite3
import argparse

import pandas as pd
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

TAXA_CSV = os.path.join(ROOT, "data", "taxa.csv")
META_CSV = os.path.join(ROOT, "output", "images_meta.csv")
# 注意：元数据里的 image_path 是相对 output/ 的（形如 images/00/08/xxx.webp），
# 不是相对 output/images/。最初写错成后者，导致 6808 张图全部找不到源文件。
SRC_ROOT = os.path.join(ROOT, "output")

DIST = os.path.join(ROOT, "dist_assets")
DB_PATH = os.path.join(DIST, "bio.db")
IMG_OUT = os.path.join(DIST, "images")
MANIFEST = os.path.join(DIST, "manifest.json")

TAX_COLS = ["kingdom_latin", "kingdom_cn", "phylum_latin", "phylum_cn",
            "class_latin", "class_cn", "order_latin", "order_cn",
            "family_latin", "family_cn", "genus_latin", "genus_cn"]

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;

-- 分类阶元字典：12 个字符串列 -> 一行一个组合，主表只引用 tax_id
CREATE TABLE tax (
    tax_id        INTEGER PRIMARY KEY,
    kingdom_latin TEXT, kingdom_cn TEXT,
    phylum_latin  TEXT, phylum_cn  TEXT,
    class_latin   TEXT, class_cn   TEXT,
    order_latin   TEXT, order_cn   TEXT,
    family_latin  TEXT, family_cn  TEXT,
    genus_latin   TEXT, genus_cn   TEXT
);

-- 图片字典：author 这类字符串重复度高，抽出来单独存
CREATE TABLE img (
    img_id      INTEGER PRIMARY KEY,
    image_path  TEXT,
    author      TEXT,
    license     TEXT,
    license_url TEXT,
    page_url    TEXT
);

CREATE TABLE entry (
    id          INTEGER PRIMARY KEY,
    sci_name    TEXT NOT NULL,
    cn_name     TEXT,
    tax_id      INTEGER,
    img_id      INTEGER,
    source      TEXT,
    taxon_id    INTEGER,
    species_sci TEXT,
    is_subsp    INTEGER,
    has_image   INTEGER
);
"""

INDEXES = """
CREATE INDEX idx_sci     ON entry(sci_name);
CREATE INDEX idx_cn      ON entry(cn_name);
CREATE INDEX idx_species ON entry(species_sci);
CREATE INDEX idx_hasimg  ON entry(has_image);
CREATE INDEX idx_tax     ON entry(tax_id);
CREATE INDEX idx_img     ON entry(img_id);
CREATE INDEX idx_family  ON tax(family_cn);
CREATE INDEX idx_genus   ON tax(genus_latin);
"""


def build_db(size, quality, fresh=False):
    print("读取分类名录 …")
    taxa = pd.read_csv(TAXA_CSV, dtype=str).fillna("")
    print(f"  taxa.csv: {len(taxa):,} 行")

    print("构建分类阶元字典 …")
    tax_rows = taxa[TAX_COLS].drop_duplicates().reset_index(drop=True)
    tax_rows.insert(0, "tax_id", range(1, len(tax_rows) + 1))
    taxa = taxa.merge(tax_rows, on=TAX_COLS, how="left")
    print(f"  12 列字符串 -> {len(tax_rows):,} 个唯一阶元组合")

    img_rows = None
    if os.path.exists(META_CSV):
        meta = pd.read_csv(META_CSV, dtype=str).fillna("")
        meta = meta[meta["status"] == "ok"].copy()
        for c in ("image_path", "author", "license", "license_url", "page_url"):
            if c not in meta.columns:
                meta[c] = ""
        meta = meta[["sci_name", "image_path", "author", "license",
                     "license_url", "page_url"]]
        meta = meta.rename(columns={"sci_name": "species_sci"})
        meta = meta.drop_duplicates("species_sci", keep="first")

        print("构建图片字典 …")
        img_rows = meta[["image_path", "author", "license",
                         "license_url", "page_url"]].drop_duplicates().reset_index(drop=True)
        img_rows.insert(0, "img_id", range(1, len(img_rows) + 1))
        meta = meta.merge(img_rows, on=["image_path", "author", "license",
                                        "license_url", "page_url"], how="left")
        taxa = taxa.merge(meta[["species_sci", "img_id"]], on="species_sci", how="left")
        print(f"  {len(meta):,} 个有图物种 -> {len(img_rows):,} 条图片记录")
    else:
        print(f"  未找到 {META_CSV}，数据库不含图片信息")
        taxa["img_id"] = None

    taxa["has_image"] = taxa["img_id"].notna().astype(int)
    taxa["is_subsp"] = (taxa["is_subsp"].astype(str).str.lower() == "true").astype(int)
    taxa["id"] = range(1, len(taxa) + 1)

    # 只在明确要求重建时清空 dist_assets。
    # 这里踩过一次坑：原先无条件 rmtree，于是 --db-only 会把已经压好的
    # 6808 张图（约 20 分钟的工作量）一起删掉，而且因为 --db-only 跳过了
    # 压缩，最终得到一个没有图、也没有报告缺失的残缺资产目录。
    if fresh and os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    print("写 SQLite …")
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    tax_rows.to_sql("tax", conn, if_exists="append", index=False)
    if img_rows is not None:
        img_rows.to_sql("img", conn, if_exists="append", index=False)

    entry = taxa[["id", "sci_name", "cn_name", "tax_id", "img_id", "source",
                  "taxon_id", "species_sci", "is_subsp", "has_image"]].copy()
    # sqlite 不接受 NaN，统一成 None
    for c in ("cn_name", "source", "species_sci"):
        entry[c] = entry[c].replace({"": None})
    entry["img_id"] = entry["img_id"].where(entry["img_id"].notna(), None)
    entry.to_sql("entry", conn, if_exists="append", index=False)

    conn.executescript(INDEXES)
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()

    # 自检
    n = conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
    w = conn.execute("SELECT COUNT(*) FROM entry WHERE has_image=1").fetchone()[0]
    conn.close()

    print(f"  entry {n:,} 行，其中有图 {w:,} 行")
    print(f"  bio.db: {os.path.getsize(DB_PATH)/1024/1024:.1f} MB")
    return taxa, img_rows


def rel_to_assets(rel):
    """
    把元数据里的规范路径（相对 output/，形如 images/00/08/xxx.webp）
    映射成 dist_assets/ 下的相对路径（形如 00/08/xxx.webp）。

    这样 dist_assets/images/ 的内容就与 bio.db 里 image_path 去掉 "images/"
    前缀后的部分一一对应，App 端用 IMG_BASE='assets/images/' + 去掉前缀的路径
    即可取到图。
    """
    r = rel.replace("\\", "/").lstrip("/")
    if r.startswith("images/"):
        r = r[len("images/"):]
    return r


def arrange_images(img_rows):
    """
    把已经压缩好的图片（可能还是旧的嵌套结构）按正确结构重排。
    兼容两种来源：dist_assets/images/images/xx/yy/...（旧的错误嵌套）
    与 output/images/xx/yy/...（原图）。只搬移，不重新编码。
    """
    wanted = {rel_to_assets(p) for p in img_rows["image_path"].unique() if p}
    moved = 0
    for rel in sorted(wanted):
        dst = os.path.join(IMG_OUT, rel.replace("/", os.sep))
        if os.path.exists(dst):
            continue
        # 旧嵌套位置
        legacy = os.path.join(IMG_OUT, "images", rel.replace("/", os.sep))
        if os.path.exists(legacy):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(legacy, dst)
            moved += 1
    # 清掉可能残留的空嵌套目录
    legacy_root = os.path.join(IMG_OUT, "images")
    if os.path.isdir(legacy_root):
        shutil.rmtree(legacy_root, ignore_errors=True)
    if moved:
        print(f"重排 {moved:,} 张图片到正确目录结构")
    return moved


def count_images():
    n = 0
    b = 0
    if os.path.isdir(IMG_OUT):
        for dp, _, fs in os.walk(IMG_OUT):
            for fn in fs:
                n += 1
                b += os.path.getsize(os.path.join(dp, fn))
    return n, b


def compress_images(img_rows, size, quality, limit=0):
    if img_rows is None or len(img_rows) == 0:
        print("没有图片记录，跳过压缩")
        return 0, 0

    paths = sorted(p for p in img_rows["image_path"].unique() if p)
    if limit:
        paths = paths[:limit]

    print(f"压缩 {len(paths):,} 张图片 → 最长边 {size}px, WebP q{quality} …")
    os.makedirs(IMG_OUT, exist_ok=True)

    total = 0
    done = 0
    failed = []
    for i, rel in enumerate(paths, 1):
        src = os.path.join(SRC_ROOT, rel.replace("/", os.sep))
        # 目标路径要剥掉 "images/" 前缀，否则会写成 assets/images/images/...
        dst = os.path.join(IMG_OUT, rel_to_assets(rel).replace("/", os.sep))
        if not os.path.exists(src):
            failed.append(rel)
            continue
        try:
            im = Image.open(src)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            im.save(dst, "WEBP", quality=quality, method=6)
            total += os.path.getsize(dst)
            done += 1
        except Exception as e:
            failed.append(f"{rel} ({e})")

        if i % 500 == 0 or i == len(paths):
            print(f"  {i:,}/{len(paths):,}  已写出 {total/1024/1024:.1f} MB")

    print(f"  成功 {done:,} 张，合计 {total/1024/1024:.1f} MB，"
          f"平均 {total/max(1,done)/1024:.1f} KB")
    if failed:
        print(f"  失败 {len(failed)} 张，前 5 个：{failed[:5]}")
    return done, total


def write_manifest(taxa, img_count, img_bytes, size, quality):
    species = int(taxa["species_sci"].nunique())
    with_img = int(taxa.loc[taxa["has_image"] == 1, "species_sci"].nunique())
    db_bytes = os.path.getsize(DB_PATH)
    m = {
        "tool": "bio-tool",
        "image_max_px": size,
        "image_quality": quality,
        "rows": int(len(taxa)),
        "species": species,
        "species_with_image": with_img,
        "images": img_count,
        "images_bytes": img_bytes,
        "db_bytes": db_bytes,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 58)
    print(f"总行数（含亚种）: {m['rows']:,}")
    print(f"物种级去重      : {m['species']:,}")
    print(f"有图物种        : {m['species_with_image']:,}"
          f" ({100.0*m['species_with_image']/m['species']:.2f}%)")
    print(f"图片            : {img_count:,} 张, {img_bytes/1024/1024:.1f} MB")
    print(f"数据库          : {db_bytes/1024/1024:.1f} MB")
    print(f"资产总计        : {(img_bytes + db_bytes)/1024/1024:.1f} MB")
    print("=" * 58)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=256, help="图片最长边，默认 256")
    ap.add_argument("--quality", type=int, default=65, help="WebP 质量，默认 65")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张图")
    ap.add_argument("--db-only", action="store_true",
                    help="只重建数据库与目录结构，不重新压缩图片")
    ap.add_argument("--fresh", action="store_true",
                    help="清空 dist_assets/ 后重建（会删掉已压缩的图片，需重新压）")
    args = ap.parse_args()

    if not os.path.exists(TAXA_CSV):
        raise SystemExit(f"缺少 {TAXA_CSV}")

    taxa, img_rows = build_db(args.size, args.quality, fresh=args.fresh)
    if args.db_only:
        # 图片已在磁盘上，只是目录结构可能被重新安排过，原地重排
        if img_rows is not None and os.path.isdir(SRC_IMAGES):
            arrange_images(img_rows)
        n, b = count_images()
    else:
        n, b = compress_images(img_rows, args.size, args.quality, args.limit)
    write_manifest(taxa, n, b, args.size, args.quality)


if __name__ == "__main__":
    main()
