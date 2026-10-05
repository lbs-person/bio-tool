# scripts/build_db.py
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

taxa = pd.read_csv(TAXA_CSV, dtype=str).fillna("")

if os.path.exists(META_CSV):
    meta = pd.read_csv(META_CSV, dtype=str).fillna("")
    meta = meta[["sci_name", "image_path", "author",
                 "license", "license_url", "page_url"]]
    meta = meta.rename(columns={"sci_name": "species_sci"})
    meta = meta.drop_duplicates("species_sci", keep="first")
    taxa = taxa.merge(meta, on="species_sci", how="left")
else:
    for c in ["image_path", "author", "license", "license_url", "page_url"]:
        taxa[c] = ""

for c in ["image_path", "author", "license", "license_url", "page_url"]:
    taxa[c] = taxa[c].fillna("")

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
taxa.to_sql("taxa", conn, if_exists="replace", index=False)
conn.execute("CREATE INDEX idx_taxa_sci ON taxa(sci_name)")
conn.execute("CREATE INDEX idx_taxa_cn ON taxa(cn_name)")
conn.execute("CREATE INDEX idx_taxa_species ON taxa(species_sci)")
conn.commit()
conn.close()

total = len(taxa)
with_img = (taxa["image_path"] != "").sum()
print(f"数据库: {DB_PATH}")
print(f"总行数: {total}")
print(f"有图: {with_img}")