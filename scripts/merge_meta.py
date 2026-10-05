# scripts/merge_meta.py
"""
把所有分片元数据合并成一份 output/images_meta.csv。

分片文件命名为 images_meta_sh<分片>_of<总分片>.csv。
同一个学名可能出现多条记录（不同时间抓过、状态不同），合并规则：
  1. status=ok 优先于其他状态；
  2. 同为 ok 时保留 image_bytes 更大的一张（分辨率留存更好）；
  3. 其余状态按出现顺序保留第一条，便于后续重试时知道为什么没抓到。
"""
import os
import re
import glob

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
OUT_DIR = os.path.join(ROOT, "output")

SHARD_RE = re.compile(r"^images_meta_sh(\d+)_of(\d+)\.csv$")

# 状态优先级：越小越优先保留。
# no_image 是历史脚本用的写法，语义等同现在的 no_photo，两者必须同优先级，
# 否则同一物种的两条记录会按不同名次去重，结果不稳定。
STATUS_RANK = {
    "ok": 0,
    "download_failed": 1,
    "no_photo": 2,
    "no_image": 2,
    "no_taxon": 3,
    "license_rejected": 3,
    "missing_file": 4,
}


def main():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "images_meta_sh*_of*.csv")))
    if not files:
        print("没有找到 images_meta_sh*_of*.csv 分片文件")
        raise SystemExit(1)

    def sort_key(p):
        m = SHARD_RE.match(os.path.basename(p))
        return (int(m.group(2)), int(m.group(1))) if m else (0, 0)

    files.sort(key=sort_key)

    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str).fillna("")
        # 历史行可能没有 schema_version / image_bytes 等列
        for col in ["schema_version", "image_bytes", "match_type", "matched_name"]:
            if col not in df.columns:
                df[col] = ""
        frames.append(df)
        print(f"  读入 {os.path.basename(f)}: {len(df)} 行")

    merged = pd.concat(frames, ignore_index=True)

    merged["_rank"] = merged["status"].map(STATUS_RANK).fillna(9).astype(int)
    merged["_bytes"] = pd.to_numeric(merged["image_bytes"], errors="coerce").fillna(0)
    merged["_is_ok"] = (merged["status"] == "ok").astype(int)

    # ok 优先，其次字节数大，最后按原始顺序
    merged = merged.sort_values(
        by=["_is_ok", "_rank", "_bytes"],
        ascending=[False, True, False],
        kind="stable",
    )
    final = merged.drop_duplicates("sci_name", keep="first")

    # 确认没有 ok 记录指向不存在的图片
    missing = []
    for _, r in final[final["status"] == "ok"].iterrows():
        rel = (r.get("image_path") or "").strip()
        if not rel:
            continue
        if not os.path.exists(os.path.join(OUT_DIR, rel.replace("/", os.sep))):
            missing.append(rel)
    if missing:
        print(f"警告：{len(missing)} 条 ok 记录的图片文件不存在，建议先跑 verify_meta.py --fix")

    final = final.drop(columns=["_rank", "_bytes", "_is_ok"])
    out = os.path.join(OUT_DIR, "images_meta.csv")
    final.to_csv(out, index=False, encoding="utf-8-sig")

    ok = (final["status"] == "ok").sum()
    print(f"合并 {len(files)} 个分片，总记录 {len(merged)}，去重后 {len(final)}")
    print(f"其中 ok: {ok}")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
