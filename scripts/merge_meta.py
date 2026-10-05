# scripts/merge_meta.py
import os
import glob
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
OUT_DIR = os.path.join(ROOT, "output")

files = sorted(glob.glob(os.path.join(OUT_DIR, "images_meta_*.csv")))
if not files:
    print("没有找到 images_meta_*.csv")
    raise SystemExit(1)

frames = [pd.read_csv(f, dtype=str).fillna("") for f in files]
merged = pd.concat(frames, ignore_index=True)

ok = merged[merged["status"] == "ok"].drop_duplicates("sci_name", keep="first")
others = merged[merged["status"] != "ok"].drop_duplicates("sci_name", keep="first")
others = others[~others["sci_name"].isin(ok["sci_name"])]

final = pd.concat([ok, others], ignore_index=True)
out = os.path.join(OUT_DIR, "images_meta.csv")
final.to_csv(out, index=False, encoding="utf-8-sig")

print(f"合并 {len(files)} 个分片")
print(f"总记录: {len(merged)}，去重后: {len(final)}")
print(f"其中 ok: {len(ok)}")
print(f"输出: {out}")