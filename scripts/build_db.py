# scripts/build_db.py
"""
把分类数据与图片元数据合成一份 SQLite 数据库 output/taxa.db。

数据关系：
  data/taxa.csv           156,107 行，含亚种；用 sci_name 作主键
  data/species_for_images.csv  143,020 个物种级去重条目
  output/images_meta.csv  图片元数据，按 sci_name 与上面两者对应

图片以 species_sci（= 种级学名）为纽带挂到所有同名行上：一个物种有图，
它的所有亚种记录都能查到这张图。
"""
import os
import sqlite3

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "output")

TAXA_CSV = os.path.join(DATA_DIR, "taxa.csv")
META_CSV = os.path.join(OUT_DIR, "images_meta.csv")
DB_PATH = os.path.join(OUT_DIR, "taxa.db")

IMG_COLS = ["image_path", "image_url", "author", "license",
            "license_url", "page_url", "image_bytes", "image_w", "image_h"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS taxa (
    sci_name      TEXT,
    cn_name       TEXT,
    kingdom_latin TEXT, kingdom_cn TEXT,
    phylum_latin  TEXT, phylum_cn  TEXT,
    class_latin   TEXT, class_cn   TEXT,
    order_latin   TEXT, order_cn   TEXT,
    family_latin  TEXT, family_cn  TEXT,
    genus_latin   TEXT, genus_cn   TEXT,
    source        TEXT,
    taxon_id      INTEGER,
    species_sci   TEXT,
    is_subsp      TEXT,
    image_path    TEXT, image_url  TEXT,
    author        TEXT, license    TEXT, license_url TEXT,
    page_url      TEXT,
    image_bytes   INTEGER, image_w INTEGER, image_h INTEGER,
    has_image     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_taxa_sci     ON taxa(sci_name);
CREATE INDEX IF NOT EXISTS idx_taxa_cn      ON taxa(cn_name);
CREATE INDEX IF NOT EXISTS idx_taxa_species ON taxa(species_sci);
CREATE INDEX IF NOT EXISTS idx_taxa_hasimg  ON taxa(has_image);
CREATE INDEX IF NOT EXISTS idx_taxa_family  ON taxa(family_cn);
"""


def main():
    taxa = pd.read_csv(TAXA_CSV, dtype=str).fillna("")

    if os.path.exists(META_CSV):
        meta = pd.read_csv(META_CSV, dtype=str).fillna("")
        meta = meta[meta["status"] == "ok"].copy()
        if "image_bytes" not in meta.columns:
            meta["image_bytes"] = ""
        if "image_w" not in meta.columns:
            meta["image_w"] = ""
        if "image_h" not in meta.columns:
            meta["image_h"] = ""
        keep = ["sci_name"] + [c for c in IMG_COLS if c in meta.columns]
        meta = meta[keep].drop_duplicates("sci_name", keep="first")
        meta = meta.rename(columns={"sci_name": "species_sci"})
        taxa = taxa.merge(meta, on="species_sci", how="left")
        print(f"合并图片元数据: {len(meta)} 个物种有图")
    else:
        print(f"未找到 {META_CSV}，数据库将不含图片信息")
        for c in IMG_COLS:
            taxa[c] = ""

    for c in IMG_COLS:
        if c not in taxa.columns:
            taxa[c] = ""
        taxa[c] = taxa[c].fillna("").astype(str).replace({"nan": ""})

    taxa["has_image"] = (taxa["image_path"].str.strip() != "").astype(int)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    taxa.to_sql("taxa", conn, if_exists="append", index=False)
    conn.commit()

    total = len(taxa)
    with_img = int(taxa["has_image"].sum())
    species_total = taxa["species_sci"].nunique()
    species_with = taxa.loc[taxa["has_image"] == 1, "species_sci"].nunique()

    conn.close()

    print(f"数据库: {DB_PATH}")
    print(f"总行数: {total}（含亚种）")
    print(f"物种级: {species_total}")
    print(f"有图行: {with_img}")
    print(f"有图物种: {species_with} / {species_total}"
          f"（{100.0 * species_with / max(1, species_total):.2f}%）")


if __name__ == "__main__":
    main()
