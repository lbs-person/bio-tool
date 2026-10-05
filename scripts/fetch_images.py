# scripts/fetch_images.py
"""
从 iNaturalist 抓图，跑在 GitHub Actions 上。
- 按学名搜物种，拿 taxon_id
- 拉该物种的观察照片
- 只保留 CC0 / CC BY / CC BY-SA
- 分片、断点续传
"""
import os
import re
import csv
import time
import argparse
from io import BytesIO

import pandas as pd
import requests
from PIL import Image
from tqdm import tqdm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

SPECIES_CSV = os.path.join(ROOT, "data", "species_for_images.csv")
OUT_DIR = os.path.join(ROOT, "output")
IMAGES_DIR = os.path.join(OUT_DIR, "images")

USER_AGENT = "BioOfflineImageBot/1.0 (https://github.com/lbs-person/bio-tool)"
SLEEP = 0.3
MAX_SIZE = 800
WEBP_QUALITY = 80
ALLOWED = {"cc0", "cc-by", "cc-by-sa"}


def load_done(meta_csv):
    """只把 status == 'ok' 的记录当已完成"""
    if not os.path.exists(meta_csv):
        return set()
    done = set()
    with open(meta_csv, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "ok":
                done.add(row["sci_name"])
    return done


def shard_path(tid):
    s = str(tid).zfill(8)
    return f"images/{s[0:2]}/{s[2:4]}/{s}.webp"


def inat_taxon_id(sci_name):
    """用学名搜 iNat taxon_id"""
    try:
        r = requests.get(
            "https://api.inaturalist.org/v1/taxa",
            params={"q": sci_name, "rank": "species,subspecies", "per_page": 3},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        for res in data.get("results", []):
            name = res.get("name", "")
            # 优先精确匹配学名
            if name.lower() == sci_name.lower():
                return res["id"]
        # 没有精确匹配，取第一个
        if data.get("results"):
            return data["results"][0]["id"]
    except Exception:
        return None
    return None


def inat_photo(taxon_id):
    """拉该物种的观察照片，返回第一个合规的"""
    try:
        r = requests.get(
            "https://api.inaturalist.org/v1/observations",
            params={
                "taxon_id": taxon_id,
                "photos": "true",
                "per_page": 5,
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
            url = photo.get("url", "")
            if not url:
                continue
            # square → large（1024px）
            url = url.replace("/square.", "/large.")
            author = photo.get("attribution", "") or ""
            return {
                "url": url,
                "author": author,
                "license": lic,
                "license_url": f"https://creativecommons.org/licenses/{lic}/4.0/",
                "page_url": f"https://www.inaturalist.org/observations/{obs.get('id')}",
            }
    return None


def download_and_compress(url, out_path):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        if r.status_code != 200:
            return False
        img = Image.open(BytesIO(r.content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_SIZE:
            if w >= h:
                img = img.resize((MAX_SIZE, int(h * MAX_SIZE / w)),
                                 Image.Resampling.LANCZOS)
            else:
                img = img.resize((int(w * MAX_SIZE / h), MAX_SIZE),
                                 Image.Resampling.LANCZOS)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, "WEBP", quality=WEBP_QUALITY, method=6)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shard-total", type=int, default=1)
    ap.add_argument("--count", type=int, default=999999)
    args = ap.parse_args()

    os.makedirs(IMAGES_DIR, exist_ok=True)
    meta_csv = os.path.join(OUT_DIR, f"images_meta_{args.shard:02d}.csv")

    done = load_done(meta_csv)
    species = pd.read_csv(SPECIES_CSV, dtype=str).fillna("").to_dict("records")

    # 分片：按全局 index 取模
    shard_species = [sp for i, sp in enumerate(species)
                     if i % args.shard_total == args.shard]
    todo = [sp for sp in shard_species
            if sp["sci_name_for_image"].strip() not in done][:args.count]

    print(f"shard {args.shard}/{args.shard_total}: "
          f"本片 {len(shard_species)}，已完成(ok) {len(done)}，本次 {len(todo)}")

    fields = ["taxon_id", "sci_name", "cn_name", "image_path", "image_url",
              "author", "license", "license_url", "page_url", "status"]
    mode = "a" if done else "w"
    mf = open(meta_csv, mode, encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(mf, fieldnames=fields)
    if mode == "w":
        writer.writeheader()

    ok_count = 0
    for sp in tqdm(todo, desc=f"shard {args.shard}"):
        sci = sp["sci_name_for_image"].strip()
        cn = sp["cn_name"].strip()
        tid = int(sp["taxon_id"])

        tkey = inat_taxon_id(sci)
        time.sleep(SLEEP)

        if not tkey and " " in sci:
            parts = sci.split()
            fb = f"{parts[0]} {parts[1]}"
            if fb != sci:
                tkey = inat_taxon_id(fb)
                time.sleep(SLEEP)

        info = None
        if tkey:
            info = inat_photo(tkey)
            time.sleep(SLEEP)

        if not info:
            writer.writerow({"taxon_id": tid, "sci_name": sci, "cn_name": cn,
                             "status": "no_image"})
            mf.flush()
            continue

        rel = shard_path(tid)
        ok = download_and_compress(info["url"], os.path.join(OUT_DIR, rel))

        writer.writerow({
            "taxon_id": tid, "sci_name": sci, "cn_name": cn,
            "image_path": rel if ok else "",
            "image_url": info["url"],
            "author": info["author"],
            "license": info["license"],
            "license_url": info["license_url"],
            "page_url": info["page_url"],
            "status": "ok" if ok else "download_failed",
        })
        if ok:
            ok_count += 1
        mf.flush()
        time.sleep(SLEEP)

    mf.close()
    print(f"shard {args.shard} 成功: {ok_count} 张")


if __name__ == "__main__":
    main()