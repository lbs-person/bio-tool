# scripts/gui.py
"""
离线生物分类查询工具的图形界面。

依赖标准库 tkinter，图片预览需要 Pillow（缺失时仍可正常浏览文字信息）。

用法：
    python scripts/gui.py
    python scripts/gui.py --db output/taxa.db --output output
"""
import os
import sys
import sqlite3
import argparse
import tkinter as tk
from tkinter import ttk, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_DB = os.path.join(ROOT, "output", "taxa.db")
# image_path 相对 output/ 存放，图片位于其下的 images/
DEFAULT_OUTPUT = os.path.join(ROOT, "output")

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

LEVELS = [
    ("kingdom_cn", "界"),
    ("phylum_cn", "门"),
    ("class_cn", "纲"),
    ("order_cn", "目"),
    ("family_cn", "科"),
    ("genus_cn", "属"),
]

PAGE_SIZE = 200


class App:
    def __init__(self, root, db_path, output_dir):
        self.root = root
        self.db_path = db_path
        self.output_dir = output_dir
        self.conn = None
        self.rows = []
        self.photo = None

        root.title("离线生物分类查询工具")
        root.geometry("1080x680")

        self._build_toolbar()
        self._build_body()
        self._build_status()

        self.open_db()
        self.search()

    # ---------- 界面 ----------

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")

        ttk.Label(bar, text="搜索").pack(side="left")
        self.var_q = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.var_q, width=26)
        entry.pack(side="left", padx=(6, 12))
        entry.bind("<Return>", lambda e: self.search())

        self.var_levels = {}
        for col, label in LEVELS:
            ttk.Label(bar, text=label).pack(side="left")
            var = tk.StringVar()
            self.var_levels[col] = var
            e = ttk.Entry(bar, textvariable=var, width=9)
            e.pack(side="left", padx=(3, 8))
            e.bind("<Return>", lambda ev: self.search())

        self.var_with_image = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="只看有图", variable=self.var_with_image).pack(side="left", padx=6)

        ttk.Button(bar, text="查询", command=self.search).pack(side="left", padx=4)
        ttk.Button(bar, text="重置", command=self.reset).pack(side="left")

    def _build_body(self):
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = ttk.Frame(paned)
        cols = ("sci", "cn")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("sci", text="学名")
        self.tree.heading("cn", text="中文名")
        self.tree.column("sci", width=250, anchor="w")
        self.tree.column("cn", width=150, anchor="w")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        paned.add(left, weight=3)

        right = ttk.Frame(paned, padding=(10, 0))
        self.lbl_image = ttk.Label(right, text="（无图片）", anchor="center",
                                   background="#f2f2f2", width=38)
        self.lbl_image.pack(fill="x")

        self.txt = tk.Text(right, wrap="word", height=18, width=46,
                           font=("Microsoft YaHei UI", 9))
        self.txt.pack(fill="both", expand=True, pady=(8, 0))
        self.txt.configure(state="disabled")
        paned.add(right, weight=2)

    def _build_status(self):
        self.var_status = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.var_status,
                  padding=(12, 4), anchor="w").pack(fill="x")

    # ---------- 数据 ----------

    def open_db(self):
        if not os.path.exists(self.db_path):
            messagebox.showerror(
                "找不到数据库",
                f"{self.db_path}\n\n请先运行：\n"
                f"  python scripts/merge_meta.py\n  python scripts/build_db.py")
            sys.exit(1)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.var_status.set(f"数据库：{self.db_path}")

    def search(self):
        where, params = [], []
        q = self.var_q.get().strip()
        if q:
            where.append("(sci_name LIKE ? OR cn_name LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        for col, _ in LEVELS:
            v = self.var_levels[col].get().strip()
            if v:
                where.append(f"{col} LIKE ?")
                params.append(f"%{v}%")
        if self.var_with_image.get():
            where.append("has_image = 1")

        sql = "SELECT * FROM taxa"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY (cn_name = ''), cn_name, sci_name LIMIT ?"
        params.append(PAGE_SIZE)

        self.rows = self.conn.execute(sql, params).fetchall()

        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i),
                             values=(r["sci_name"], r["cn_name"] or "—"))
        total = self.conn.execute("SELECT COUNT(*) FROM taxa").fetchone()[0]
        with_img = self.conn.execute(
            "SELECT COUNT(DISTINCT species_sci) FROM taxa WHERE has_image = 1").fetchone()[0]
        species = self.conn.execute(
            "SELECT COUNT(DISTINCT species_sci) FROM taxa").fetchone()[0]
        self.var_status.set(
            f"命中 {len(self.rows)} 条（上限 {PAGE_SIZE}）　|　"
            f"库内 {total:,} 行 / {species:,} 物种　|　有图 {with_img:,} "
            f"({100.0 * with_img / max(1, species):.2f}%)")

        if self.rows:
            self.tree.selection_set("0")
            self.on_select()

    def reset(self):
        self.var_q.set("")
        for var in self.var_levels.values():
            var.set("")
        self.var_with_image.set(False)
        self.search()

    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.rows[int(sel[0])]
        self.show_detail(row)

    def show_detail(self, row):
        chain = []
        for col, label in LEVELS:
            v = (row[col] or "").strip()
            if v:
                chain.append(f"{label}: {v}")

        lines = [
            f"中文名　{row['cn_name'] or '—'}",
            f"学　名　{row['sci_name']}",
            f"物种名　{row['species_sci']}",
        ]
        if row["is_subsp"] == "True":
            lines.append("类　别　亚种")
        lines.append("")
        lines.append("　".join(chain))
        lines.append("")
        src = (row["source"] or "").strip()
        if src:
            lines.append(f"数据来源　{src}")
        lines.append("")
        if (row["image_path"] or "").strip():
            lines.append(f"图片路径　{row['image_path']}")
            if row["author"]:
                lines.append(f"作者　　　{row['author']}")
            if row["license"]:
                lines.append(f"许可　　　{row['license']}")
            if row["license_url"]:
                lines.append(f"许可链接　{row['license_url']}")
            if row["page_url"]:
                lines.append(f"来源页　　{row['page_url']}")
            if row["image_bytes"]:
                try:
                    kb = int(float(row["image_bytes"])) / 1024
                    lines.append(f"文件大小　{kb:.1f} KB"
                                 f"　{row['image_w']}×{row['image_h']}")
                except (TypeError, ValueError):
                    pass
        else:
            lines.append("图片　　　无")

        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(lines))
        self.txt.configure(state="disabled")

        self.show_image(row)

    def show_image(self, row):
        self.photo = None
        rel = (row["image_path"] or "").strip()
        if not rel:
            self.lbl_image.configure(image="", text="（无图片）", height=4)
            return
        if not HAS_PIL:
            self.lbl_image.configure(image="", text=f"（未安装 Pillow，无法预览）\n{rel}", height=4)
            return

        path = os.path.join(self.output_dir, rel.replace("/", os.sep))
        if not os.path.exists(path):
            self.lbl_image.configure(
                image="", text=f"（图片文件缺失）\n{rel}\n请确认 --output 指向正确的 output 目录", height=4)
            return
        try:
            img = Image.open(path)
            img.thumbnail((420, 420), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)
            self.lbl_image.configure(image=self.photo, text="", height=self.photo.height())
        except Exception as exc:
            self.lbl_image.configure(image="", text=f"（图片读取失败：{exc}）", height=4)


def main():
    ap = argparse.ArgumentParser(description="离线生物分类查询工具（图形界面）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--output", default=DEFAULT_OUTPUT,
                    help="output 目录（image_path 相对此目录）")
    args = ap.parse_args()

    root = tk.Tk()
    App(root, args.db, args.output)
    root.mainloop()


if __name__ == "__main__":
    main()
