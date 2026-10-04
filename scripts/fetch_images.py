"""
从维基抓图，支持分片并发（GitHub Actions matrix）。
每片独立处理，输出 images_meta_XX.csv，最后合并。
"""
import os
import re
import csv
import time
import argparse
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from PIL import Image
from tqdm import tqdm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

SPECIES_CSV = os.path.join(ROOT, "data", "species_for_images.csv")
OUT_DIR = os.path.join(ROOT, "output")
IMAGES_DIR = os.path.join(OUT_DIR, "images")

LANG = "en"
USER_AGENT = "BioOfflineImageBot/1.0 (https://github.com/lbs-person/bio-tool; contact@example.com)"
SLEEP = 1.0
MAX_SIZE = 800
WEBP_QUALITY = 80
THREADS = 1
ALLOWED_LICENSES = ["cc0", "public domain", "cc by", "cc by-sa"]


def is_allowed(name):
    if not name:
        return False
    s = name.lower().replace(" ", "")
    if "nc" in s or "nd" in s:
        return False
    return any(ok.replace(" ", "") in s for ok in ALLOWED_LICENSES)


def shard_path(tid):
    s = str(tid).zfill(8)
    return f"images/{s[0:2]}/{s[2:4]}/{s}.webp"


def fetch_wiki(sci, lang):
    api = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT}

    try:
        r = requests.get(api, params={
            "action": "query", "format": "json",
            "titles": sci, "redirects": 1,
            "prop": "pageimages",
            "piprop": "thumbnail|name",
            "pithumbsize": MAX_SIZE,
        }, headers=headers, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            print(f"[429] {sci} -> 等 {wait}s")
            time.sleep(wait)
            r = requests.get(api, params={
                "action": "query", "format": "json",
                "titles": sci, "redirects": 1,
                "prop": "pageimages",
                "piprop": "thumbnail|name",
                "pithumbsize": MAX_SIZE,
            }, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"[DEBUG] {sci} -> HTTP {r.status_code}")
            return None
        data = r.json()
        if "query" not in data:
            print(f"[DEBUG] {sci} -> no query: {str(data)[:200]}")
            return None
    except Exception as e:
        print(f"[DEBUG] {sci} -> {type(e).__name__}: {e}")
        return None

    thumb = None
    filename = None
    for p in data.get("query", {}).get("pages", {}).values():
        if "thumbnail" in p:
            thumb = p["thumbnail"]["source"]
            filename = "File:" + p.get("pageimage", "")
            break
    if not thumb:
        return None

    author = ""
    license_ = ""
    license_url = ""
    page_url = f"https://{lang}.wikipedia.org/wiki/{sci.replace(' ', '_')}"

    if filename and filename != "File:":
        time.sleep(SLEEP)
        try:
            r2 = requests.get(api, params={
                "action": "query", "format": "json",
                "titles": filename,
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            }, headers=headers, timeout=30)
            data2 = r2.json()
            for p in data2.get("query", {}).get("pages", {}).values():
                infos = p.get("imageinfo")
                if not infos:
                    continue
                meta = infos[0].get("extmetadata", {})
                author = re.sub(r"<[^>]+>", "",
                                meta.get("Artist", {}).get("value", "")).strip()
                license_ = meta.get("LicenseShortName", {}).get("value", "")
                license_url = meta.get("LicenseUrl", {}).get("value", "")
                page_url = f"https://{lang}.wikipedia.org/wiki/{filename.replace(' ', '_')}"
        except Exception:
            pass

    return {"url": thumb, "author": author, "license": license_,
            "license_url": license_url, "page_url": page_url}


def download(url, path):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, "WEBP", quality=WEBP_QUALITY, method=6)
        return True
    except Exception:
        return False


def process_one(sp):
    sci = sp["sci_name_for_image"].strip()
    cn = sp["cn_name"].strip()
    tid = int(sp["taxon_id"])

    info = fetch_wiki(sci, LANG)
    if not info and " " in sci:
        parts = sci.split()
        fb = f"{parts[0]} {parts[1]}"
        if fb != sci:
            time.sleep(SLEEP)
            info = fetch_wiki(fb, LANG)

    if not info:
        return {"taxon_id": tid, "sci_name": sci, "cn_name": cn,
                "status": "no_image"}

    if not is_allowed(info.get("license", "")):
        return {
            "taxon_id": tid, "sci_name": sci, "cn_name": cn,
            "image_url": info["url"], "author": info.get("author", ""),
            "license": info.get("license", ""),
            "license_url": info.get("license_url", ""),
            "page_url": info.get("page_url", ""),
            "status": "license_rejected",
        }

    rel = shard_path(tid)
    ok = download(info["url"], os.path.join(OUT_DIR, rel))

    return {
        "taxon_id": tid, "sci_name": sci, "cn_name": cn,
        "image_path": rel if ok else "",
        "image_url": info["url"],
        "author": info.get("author", ""),
        "license": info.get("license", ""),
        "license_url": info.get("license_url", ""),
        "page_url": info.get("page_url", ""),
        "status": "ok" if ok else "download_failed",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-idx", type=int, required=True)
    ap.add_argument("--shard-total", type=int, required=True)
    ap.add_argument("--count", type=int, default=0, help="调试用，0 表示不限")
    args = ap.parse_args()

    os.makedirs(IMAGES_DIR, exist_ok=True)

    meta_csv = os.path.join(OUT_DIR, f"images_meta_{args.shard_idx:02d}.csv")
    fields = ["taxon_id", "sci_name", "cn_name", "image_path", "image_url",
              "author", "license", "license_url", "page_url", "status"]

    species = pd.read_csv(SPECIES_CSV, dtype=str).fillna("").to_dict("records")

    # 分片：按索引取模
    my_species = [sp for i, sp in enumerate(species)
                  if i % args.shard_total == args.shard_idx]
    if args.count:
        my_species = my_species[:args.count]

    print(f"分片 {args.shard_idx}/{args.shard_total}，本片 {len(my_species)} 种")

    mf = open(meta_csv, "w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(mf, fieldnames=fields)
    writer.writeheader()

    ok_count = 0
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futures = [ex.submit(process_one, sp) for sp in my_species]
        for f in tqdm(as_completed(futures), total=len(futures),
                      desc=f"shard {args.shard_idx}"):
            try:
                row = f.result()
                writer.writerow(row)
                mf.flush()
                if row["status"] == "ok":
                    ok_count += 1
            except Exception:
                pass

    mf.close()
    print(f"分片 {args.shard_idx} 完成，成功 {ok_count} 张")


if __name__ == "__main__":
    main()