# scripts/query.py
"""
离线生物分类查询工具的查询入口。

数据来自 output/taxa.db（由 build_db.py 生成）。图片默认从 output/images/
读取；如果图片没跟着仓库一起分发，可以把 output/ 解压到别处后用
--output 指向它。image_path 存的是相对 output/ 的路径。

用法：
    python scripts/query.py stats
    python scripts/query.py search 大熊猫
    python scripts/query.py search Panthera --kingdom 动物界 --limit 20
    python scripts/query.py search 猫 --family 猫科
    python scripts/query.py info "Panthera tigris"
    python scripts/query.py export --family 猫科 --out cats.csv
"""
import os
import sys
import csv
import sqlite3
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_DB = os.path.join(ROOT, "output", "taxa.db")
# image_path 存的是相对 output/ 的路径（形如 images/00/08/00083415.webp），
# 因此解析基准是 output 目录，不是 output/images。
DEFAULT_OUTPUT = os.path.join(ROOT, "output")

LEVELS = [
    ("kingdom_cn", "kingdom_latin", "界"),
    ("phylum_cn", "phylum_latin", "门"),
    ("class_cn", "class_latin", "纲"),
    ("order_cn", "order_latin", "目"),
    ("family_cn", "family_latin", "科"),
    ("genus_cn", "genus_latin", "属"),
]

# 各分类阶元的中文列名，键名与 argparse 的 dest 保持一致
FILTER_COLS = {
    "kingdom": "kingdom_cn",
    "phylum": "phylum_cn",
    "class_cn": "class_cn",
    "order": "order_cn",
    "family": "family_cn",
    "genus": "genus_cn",
}


def connect(db_path):
    if not os.path.exists(db_path):
        raise SystemExit(
            f"找不到数据库 {db_path}\n"
            f"请先运行：python scripts/merge_meta.py && python scripts/build_db.py")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def have_image(row, output_dir):
    rel = (row["image_path"] or "").strip()
    if not rel or not output_dir:
        return None
    p = os.path.join(output_dir, rel.replace("/", os.sep))
    return p if os.path.exists(p) else None


def format_row(row, index=None, output_dir=None, show_image=True):
    prefix = f"[{index}] " if index is not None else ""
    cn = (row["cn_name"] or "").strip()
    title = f"{prefix}{cn}  {row['sci_name']}"
    print(title.strip())
    if row["is_subsp"] == "True":
        print(f"      亚种 / subspecies")

    chain = []
    for cn_col, lat_col, label in LEVELS:
        cn_v = (row[cn_col] or "").strip()
        lat_v = (row[lat_col] or "").strip()
        if not cn_v and not lat_v:
            continue
        chain.append(f"{label}:{cn_v or lat_v}" + (f"({lat_v})" if cn_v and lat_v else ""))
    if chain:
        print("      " + "  ".join(chain))

    src = (row["source"] or "").strip()
    if src:
        print(f"      来源: {src}")

    if not show_image:
        return
    img = have_image(row, output_dir)
    if img:
        print(f"      图片: {img}")
        author = (row["author"] or "").strip()
        lic = (row["license"] or "").strip()
        page = (row["page_url"] or "").strip()
        if author:
            print(f"      作者: {author}")
        if lic:
            print(f"      许可: {lic}" + (f"  {row['license_url']}" if row["license_url"] else ""))
        if page:
            print(f"      来源页: {page}")
    elif (row["image_path"] or "").strip():
        print(f"      图片: 有记录但文件缺失（{row['image_path']}）")


def cmd_search(args):
    conn = connect(args.db)
    where = []
    params = []
    if args.query:
        where.append("(sci_name LIKE ? OR cn_name LIKE ?)")
        params += [f"%{args.query}%", f"%{args.query}%"]
    for key, col in FILTER_COLS.items():
        val = getattr(args, key)
        if val:
            where.append(f"{col} LIKE ?")
            params.append(f"%{val}%")
    if args.with_image:
        where.append("has_image = 1")
    if args.subsp:
        where.append("is_subsp = 'True'")

    sql = "SELECT * FROM taxa"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (cn_name = '') , cn_name, sci_name LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("没有匹配结果")
        return 1

    print(f"找到 {len(rows)} 条（最多显示 {args.limit} 条）\n")
    for i, row in enumerate(rows, 1):
        format_row(row, i, args.output)
        print()
    return 0


def cmd_info(args):
    conn = connect(args.db)
    exact = conn.execute(
        "SELECT * FROM taxa WHERE sci_name = ? OR cn_name = ?",
        (args.name, args.name)).fetchall()
    rows = exact
    if not rows:
        rows = conn.execute(
            "SELECT * FROM taxa WHERE sci_name LIKE ? OR cn_name LIKE ? LIMIT ?",
            (f"%{args.name}%", f"%{args.name}%", args.limit)).fetchall()
    if not rows:
        print(f"没有找到 {args.name}")
        return 1

    for i, row in enumerate(rows, 1):
        format_row(row, i if len(rows) > 1 else None, args.output)
        print()
    return 0


def cmd_stats(args):
    conn = connect(args.db)
    total = conn.execute("SELECT COUNT(*) FROM taxa").fetchone()[0]
    species = conn.execute("SELECT COUNT(DISTINCT species_sci) FROM taxa").fetchone()[0]
    with_img = conn.execute(
        "SELECT COUNT(DISTINCT species_sci) FROM taxa WHERE has_image = 1").fetchone()[0]

    print("=" * 56)
    print("bio-tool 数据统计")
    print("=" * 56)
    print(f"总记录（含亚种）: {total:,}")
    print(f"物种级去重      : {species:,}")
    print(f"有图物种        : {with_img:,}  "
          f"({100.0 * with_img / max(1, species):.2f}%)")
    print(f"缺图物种        : {species - with_img:,}")
    print()

    print("各分类阶元覆盖（有图 / 总数，按物种级去重）:")
    for cn_col, lat_col, label in LEVELS:
        row = conn.execute(f"""
            SELECT COUNT(DISTINCT species_sci) AS total,
                   COUNT(DISTINCT CASE WHEN has_image = 1 THEN species_sci END) AS withimg
            FROM taxa WHERE {lat_col} != ''
        """).fetchone()
        if row["total"]:
            pct = 100.0 * row["withimg"] / row["total"]
            print(f"  {label}: {row['withimg']:>6,} / {row['total']:>6,}  ({pct:5.1f}%)")
    print()

    print("图片许可分布:")
    for r in conn.execute("""
        SELECT license, COUNT(DISTINCT species_sci) AS n FROM taxa
        WHERE has_image = 1 AND license != ''
        GROUP BY license ORDER BY n DESC
    """):
        print(f"  {r['license']:<12} {r['n']:,}")
    return 0


def cmd_export(args):
    conn = connect(args.db)
    where, params = [], []
    if args.query:
        where.append("(sci_name LIKE ? OR cn_name LIKE ?)")
        params += [f"%{args.query}%", f"%{args.query}%"]
    for key, col in FILTER_COLS.items():
        val = getattr(args, key)
        if val:
            where.append(f"{col} LIKE ?")
            params.append(f"%{val}%")
    if args.with_image:
        where.append("has_image = 1")

    sql = "SELECT * FROM taxa"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("没有匹配结果")
        return 1

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([r[k] for k in r.keys()])
    print(f"已导出 {len(rows)} 行到 {args.out}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description="离线生物分类查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径")
    ap.add_argument("--output", default=DEFAULT_OUTPUT,
                    help="output 目录（image_path 相对此目录，图片在其下的 images/）")

    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_filters(p):
        p.add_argument("--kingdom", dest="kingdom", help="界（中文）")
        p.add_argument("--phylum", dest="phylum", help="门（中文）")
        p.add_argument("--class", dest="class_cn", help="纲（中文）")
        p.add_argument("--order", dest="order", help="目（中文）")
        p.add_argument("--family", dest="family", help="科（中文）")
        p.add_argument("--genus", dest="genus", help="属（中文）")
        p.add_argument("--with-image", action="store_true", help="只显示有图的")
        p.add_argument("--subsp", action="store_true", help="只显示亚种")

    s = sub.add_parser("search", help="模糊搜索")
    s.add_argument("query", nargs="?", default="", help="学名或中文名的一部分")
    s.add_argument("--limit", type=int, default=20)
    add_filters(s)
    s.set_defaults(func=cmd_search)

    i = sub.add_parser("info", help="精确/模糊查看某个物种")
    i.add_argument("name", help="学名或中文名")
    i.add_argument("--limit", type=int, default=10)
    i.set_defaults(func=cmd_info)

    st = sub.add_parser("stats", help="查看数据统计与覆盖率")
    st.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="导出为 CSV")
    e.add_argument("--out", required=True, help="输出 CSV 路径")
    e.add_argument("query", nargs="?", default="")
    add_filters(e)
    e.set_defaults(func=cmd_export)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
