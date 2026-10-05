"""
批量版抓图脚本。
- MediaWiki API 一次查 50 个物种
- 只把 status == 'ok' 的记录当已完成，其他状态允许重试
- 支持分片 --shard / --shard-total
"""
import os
import re
import csv
import time
import glob
import argparse
import threading
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

SPECIES_CSV = os.path.join(ROOT, "data", "species_for_images.csv")
OUT_DIR = os.path.join(ROOT, "output")
IMAGES_DIR = os.path.join(OUT_DIR, "images")

LANG = "en"
USER_AGENT = "BioOfflineImageBot/1.0 (https://github.com/lbs-person/bio-tool; contact@example.com)"
SLEEP = 0.2
BATCH_SIZE = 50
MAX_SIZE = 800
WEBP_QUALITY = 80
DOWNLOAD_THREADS = 8

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


def batch_main_images(titles, lang=LANG):
    """一次查 50 个物种的主图。返回 {title: (thumb_url, filename)}"""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT}
    titles_str = "|".join(titles)

    try:
        r = requests.get(api, params={
            "action": "query", "format": "json",
            "titles": titles_str,
            "redirects": 1,
            "prop": "pageimages",
            "piprop": "thumbnail|name",
            "pithumbsize": MAX_SIZE,
        }, headers=headers, timeout=60)
        data = r.json()
    except Exception as e:
        print(f"  batch query error: {e}")
        return {}

    redirect_map = {}
    for redir in data.get("query", {}).get("redirects", []):
        redirect_map[redir["from"]] = redir["to"]

    result = {}
    pages = data.get("query", {}).get("pages", {})
    for pid, p in pages.items():
        title = p.get("title")
        thumb = p.get("thumbnail", {}).get("source") if "thumbnail" in p else None
        pageimage = p.get("pageimage")
        filename = f"File:{pageimage}" if pageimage else None

        orig_titles = [title]
        for orig, tgt in redirect_map.items():
            if tgt == title:
                orig_titles.append(orig)

        for ot in orig_titles:
            result[ot] = (thumb, filename)

    return result


def batch_license_info(filenames, lang=LANG):
    """批量查版权。返回 {filename: {author, license, license_url}}"""
    if not filenames:
        return {}

    api = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT}
    result = {}

    for i in range(0, len(filenames), 50):
        chunk = filenames[i:i+50]
        titles_str = "|".join(chunk)

        try:
            r = requests.get(api, params={
                "action": "query", "format": "json",
                "titles": titles_str,
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            }, headers=headers, timeout=60)
            data = r.json()
        except Exception:
            continue

        for pid, p in data.get("query", {}).get("pages", {}).items():
            title = p.get("title", "")
            infos = p.get("imageinfo")
            if not infos:
                continue
            meta = infos[0].get("extmetadata", {})
            author = re.sub(r"<[^>]+>", "",
                            meta.get("Artist", {}).get("value", "")).strip()
            result[title] = {
                "author": author,
                "license": meta.get("LicenseShortName", {}).get("value", ""),
                "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            }

        time.sleep(SLEEP)

    return result


def download_one(args):
    url, path = args
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


def load_done():
    """只把 status == 'ok' 的记录当已完成，其他状态允许重试"""
    done = set()
    for f in glob.glob(os.path.join(OUT_DIR, "images_meta*.csv")):
        try:
            with open(f, "r", encoding="utf-8-sig") as fp:
                for row in csv.DictReader(fp):
                    if row.get("status") == "ok":
                        done.add(row["sci_name"])
        except Exception:
            pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shard-total", type=int, default=10)
    ap.add_argument("--count", type=int, default=500)
    args = ap.parse_args()

    meta_csv = os.path.join(OUT_DIR, f"images_meta_{args.shard:02d}.csv")
    os.makedirs(IMAGES_DIR, exist_ok=True)

    done = load_done()

    species = pd.read_csv(SPECIES_CSV, dtype=str).fillna("").to_dict("records")
    shard_species = [sp for i, sp in enumerate(species)
                     if i % args.shard_total == args.shard]
    todo = [sp for sp in shard_species
            if sp["sci_name_for_image"].strip() not in done]
    batch = todo[:args.count]

    print(f"shard {args.shard}/{args.shard_total}: "
          f"分片 {len(shard_species)}，全局已完成(ok) {len(done)}，"
          f"本分片待处理 {len(todo)}，本次 {len(batch)}")

    fields = ["taxon_id", "sci_name", "cn_name", "image_path", "image_url",
              "author", "license", "license_url", "page_url", "status"]
    mode = "a" if os.path.exists(meta_csv) else "w"
    mf = open(meta_csv, mode, encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(mf, fieldnames=fields)
    if mode == "w":
        writer.writeheader()

    lock = threading.Lock()
    ok_count = 0

    for i in range(0, len(batch), BATCH_SIZE):
        chunk = batch[i:i+BATCH_SIZE]
        titles = [sp["sci_name_for_image"].strip() for sp in chunk]

        main_map = batch_main_images(titles)
        time.sleep(SLEEP)

        files = []
        for sp in chunk:
            t = sp["sci_name_for_image"].strip()
            thumb, filename = main_map.get(t, (None, None))
            if thumb and filename:
                files.append(filename)
        license_map = batch_license_info(files)
        time.sleep(SLEEP)

        download_tasks = []
        pending_rows = []

        for sp in chunk:
            sci = sp["sci_name_for_image"].strip()
            cn = sp["cn_name"].strip()
            tid = int(sp["taxon_id"])
            thumb, filename = main_map.get(sci, (None, None))

            if not thumb:
                pending_rows.append({
                    "taxon_id": tid, "sci_name": sci, "cn_name": cn,
                    "status": "no_image"
                })
                continue

            lic = license_map.get(filename, {})
            license_name = lic.get("license", "")

            if not is_allowed(license_name):
                pending_rows.append({
                    "taxon_id": tid, "sci_name": sci, "cn_name": cn,
                    "image_url": thumb, "author": lic.get("author", ""),
                    "license": license_name,
                    "license_url": lic.get("license_url", ""),
                    "page_url": f"https://{LANG}.wikipedia.org/wiki/"
                                f"{filename.replace(' ', '_') if filename else ''}",
                    "status": "license_rejected"
                })
                continue

            rel = shard_path(tid)
            out_path = os.path.join(OUT_DIR, rel)
            download_tasks.append((thumb, out_path, tid, sci, cn, lic, filename))

        with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as ex:
            future_to_dt = {
                ex.submit(download_one, (dt[0], dt[1])): dt
                for dt in download_tasks
            }
            for fut in as_completed(future_to_dt):
                dt = future_to_dt[fut]
                try:
                    ok = fut.result()
                except Exception:
                    ok = False

                thumb, out_path, tid, sci, cn, lic, filename = dt
                rel = shard_path(tid)
                row = {
                    "taxon_id": tid, "sci_name": sci, "cn_name": cn,
                    "image_path": rel if ok else "",
                    "image_url": thumb,
                    "author": lic.get("author", ""),
                    "license": lic.get("license", ""),
                    "license_url": lic.get("license_url", ""),
                    "page_url": f"https://{LANG}.wikipedia.org/wiki/"
                                f"{filename.replace(' ', '_') if filename else ''}",
                    "status": "ok" if ok else "download_failed",
                }
                with lock:
                    writer.writerow(row)
                    mf.flush()
                if ok:
                    ok_count += 1

        for row in pending_rows:
            with lock:
                writer.writerow(row)
                mf.flush()

        processed = min(i + BATCH_SIZE, len(batch))
        print(f"  进度: {processed}/{len(batch)}，成功 {ok_count}")

    mf.close()
    print(f"shard {args.shard} 完成，成功 {ok_count} 张")


if __name__ == "__main__":
    main()