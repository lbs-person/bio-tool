"""
从维基抓图，跑在 GitHub Actions 上。
自动续传：读 output/images_meta.csv，跳过已处理的物种。
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
META_CSV = os.path.join(OUT_DIR, "images_meta.csv")

LANG = "en"
USER_AGENT = "BioOfflineImageBot/1.0 (https://github.com/yourname/yourrepo; contact@example.com)"
SLEEP = 1.0
MAX_SIZE = 800
WEBP_QUALITY = 80
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
        data = r.json()
    except Exception:
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
                return {
                    "url": thumb,
                    "author": author,
                    "license": meta.get("LicenseShortName", {}).get("value", ""),
                    "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                    "page_url": f"https://{lang}.wikipedia.org/wiki/{filename.replace(' ', '_')}",
                }
        except Exception:
            pass

    return {"url": thumb, "author": "", "license": "", "license_url": "",
            "page_url": f"https://{lang}.wikipedia.org/wiki/{sci.replace(' ', '_')}"}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3000)
    args = ap.parse_args()

    os.makedirs(IMAGES_DIR, exist_ok=True)

    done = set()
    if os.path.exists(META_CSV):
        with open(META_CSV, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                done.add(row["sci_name"])

    species = pd.read_csv(SPECIES_CSV, dtype=str).fillna("").to_dict("records")
    todo = [sp for sp in species if sp["sci_name_for_image"].strip() not in done]
    batch = todo[:args.count]

    print(f"已处理: {len(done)}，待处理: {len(todo)}，本次处理: {len(batch)}")

    fields = ["taxon_id", "sci_name", "cn_name", "image_path", "image_url",
              "author", "license", "license_url", "page_url", "status"]
    mode = "a" if done else "w"
    mf = open(META_CSV, mode, encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(mf, fieldnames=fields)
    if mode == "w":
        writer.writeheader()

    ok_count = 0
    for sp in tqdm(batch):
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
            writer.writerow({"taxon_id": tid, "sci_name": sci, "cn_name": cn,
                             "status": "no_image"})
            mf.flush()
            time.sleep(SLEEP)
            continue

        if not is_allowed(info.get("license", "")):
            writer.writerow({
                "taxon_id": tid, "sci_name": sci, "cn_name": cn,
                "image_url": info["url"], "author": info.get("author", ""),
                "license": info.get("license", ""),
                "license_url": info.get("license_url", ""),
                "page_url": info.get("page_url", ""),
                "status": "license_rejected",
            })
            mf.flush()
            time.sleep(SLEEP)
            continue

        rel = shard_path(tid)
        ok = download(info["url"], os.path.join(OUT_DIR, rel))

        writer.writerow({
            "taxon_id": tid, "sci_name": sci, "cn_name": cn,
            "image_path": rel if ok else "",
            "image_url": info["url"],
            "author": info.get("author", ""),
            "license": info.get("license", ""),
            "license_url": info.get("license_url", ""),
            "page_url": info.get("page_url", ""),
            "status": "ok" if ok else "download_failed",
        })
        if ok:
            ok_count += 1
        mf.flush()
        time.sleep(SLEEP)

    mf.close()
    print(f"本次成功: {ok_count} 张")


if __name__ == "__main__":
    main()