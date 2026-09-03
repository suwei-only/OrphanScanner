#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OrphanScanner GUI —— 孤儿文件/残留扫描器(图形界面版)
=====================================================
功能:
  - 层1 游戏平台孤儿(Steam/Epic 卸载残留,100% 确定)
  - 层2 注册表僵尸项(四重校验,高置信)
  - 层3 启发式候选(无登记大目录,仅供人工判断)

用法:
  python orphan_gui.py          # 启动图形界面
"""
import os
import sys
import threading
import queue
import shutil
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import orphan_scanner as o
except ImportError:
    # PyInstaller 打包后同目录查找
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import orphan_scanner as o

BLUE = "#1565C0"
LIGHT_BLUE = "#E3F2FD"
GREEN = "#2E7D32"
AMBER = "#E65100"
GRAY = "#757575"

CAT_COLORS = {
    "Steam孤儿": GREEN,
    "Epic孤儿": GREEN,
    "注册表残留": GREEN,
    "启发式候选": AMBER,
}

class ScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"OrphanScanner v{o.APP_VERSION} — 孤儿文件扫描器")
        self.geometry("1120x700")
        self.minsize(900, 540)
        self._queue = queue.Queue()
        self._scanning = False
        self._results = []          # 原始结构化数据
        self._rows = {}             # iid -> result dict
        o.register_global_excepthook()
        self._build_ui()
        self.after(120, self._drain_queue)

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"),
                        foreground=BLUE)
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground=GRAY)
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))

        # 顶部标题
        head = ttk.Frame(self, padding=(14, 10, 14, 4))
        head.pack(fill="x")
        ttk.Label(head, text="🛡️ OrphanScanner", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="   扫描电脑里的孤儿/残留文件", style="Hint.TLabel").pack(
            side="left", padx=(6, 0), pady=(4, 0))

        # 扫描选项栏
        opt = ttk.Frame(self, padding=(14, 4))
        opt.pack(fill="x")
        self.v1 = tk.BooleanVar(value=True)
        self.v2 = tk.BooleanVar(value=True)
        self.v3 = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="层1 游戏平台残留(Steam/Epic)", variable=self.v1).pack(side="left")
        ttk.Checkbutton(opt, text="层2 注册表僵尸项", variable=self.v2).pack(side="left", padx=8)
        ttk.Checkbutton(opt, text="层3 启发式候选(需人工判断)", variable=self.v3).pack(side="left")

        self.btn_scan = ttk.Button(opt, text="▶ 开始扫描", style="Accent.TButton",
                                   command=self._start_scan)
        self.btn_scan.pack(side="right")

        # 工具栏
        bar = ttk.Frame(self, padding=(14, 0))
        bar.pack(fill="x")
        self.btn_del = ttk.Button(bar, text="♻ 移至回收站(可还原)",
                                  command=self._delete_selected,
                                  state="disabled")
        self.btn_del.pack(side="left")
        self.btn_open = ttk.Button(bar, text="📂 打开所在文件夹", command=self._open_folder,
                                   state="disabled")
        self.btn_open.pack(side="left", padx=6)
        self.btn_trash = ttk.Button(bar, text="🗑 回收站(0)",
                                    command=self._open_trash)
        self.btn_trash.pack(side="left")
        self.btn_feedback = ttk.Button(bar, text="❓ 反馈问题",
                                       command=self._feedback)
        self.btn_feedback.pack(side="left", padx=(6, 0))
        self.btn_refresh = ttk.Button(bar, text="⟳ 重新扫描", command=self._start_scan,
                                      state="disabled")
        self.btn_refresh.pack(side="left", padx=6)
        self.lbl_state = ttk.Label(bar, text="就绪 — 点击「开始扫描」", style="Hint.TLabel")
        self.lbl_state.pack(side="right")

        # 结果表格
        wrap = ttk.Frame(self, padding=(14, 6))
        wrap.pack(fill="both", expand=True)
        cols = ("cat", "target", "note", "size")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="extended")
        heads = {"cat": ("类别", 110, "center"),
                 "target": ("路径 / 注册表键", 560, "w"),
                 "note": ("说明", 260, "w"),
                 "size": ("大小", 90, "e")}
        for cid, (txt, w, anc) in heads.items():
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=w, anchor=anc)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.tag_configure("risk", foreground=AMBER)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 底部提示
        foot = ttk.Label(self, text="⚠️ 层3 启发式候选可能包含正常软件,请核对后再删;"
                                    "删除前会检测存档目录。删除注册表键(HKLM)会弹出 UAC 授权。",
                         style="Hint.TLabel", padding=(14, 4))
        foot.pack(fill="x")

    # ---------- 扫描 ----------
    def _start_scan(self):
        if self._scanning:
            return
        layers = []
        if self.v1.get():
            layers.append(1)
        if self.v2.get():
            layers.append(2)
        if self.v3.get():
            layers.append(3)
        if not layers:
            messagebox.showinfo("提示", "请至少勾选一层扫描范围")
            return
        self._scanning = True
        self.btn_scan.config(state="disabled")
        self.btn_refresh.config(state="disabled")
        self.btn_del.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self._results, self._rows = [], {}
        self.lbl_state.config(text="扫描中,请稍候… (层3 统计大目录较慢)")
        threading.Thread(target=self._scan_worker, args=(layers,),
                         daemon=True).start()

    def _scan_worker(self, layers):
        try:
            results = o.scan_all(tuple(layers))
            self._queue.put(("done", results))
        except Exception as e:
            self._queue.put(("error", str(e)))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "done":
                    self._show_results(payload)
                elif kind == "error":
                    self._scanning = False
                    self.btn_scan.config(state="normal")
                    self.lbl_state.config(text="扫描出错")
                    messagebox.showerror("扫描失败", str(payload))
                elif kind == "delete_done":
                    self._scanning = False
                    self._after_delete(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _show_results(self, results):
        self._scanning = False
        self._results = results
        self.btn_scan.config(state="normal")
        self.btn_refresh.config(state="normal")
        for r in results:
            size = o.human(r["size"]) if r["size"] else "—"
            note = r["note"]
            if r.get("saves"):
                note += f" | ⚠含存档 {len(r['saves'])} 处"
            iid = self.tree.insert("", "end",
                                   values=(r["cat"], r["target"], note, size),
                                   tags=("risk",) if r["risk"] else ())
            self._rows[iid] = r
        cnt = len(results)
        self.lbl_state.config(text=f"完成 — 发现 {cnt} 项"
                              + ("(可勾选后删除)" if cnt else ",硬盘很干净 🎉"))
        if not cnt:
            messagebox.showinfo("扫描完成", "没有发现孤儿/残留 🎉")

    # ---------- 选择与删除(移至回收站) ----------
    def _on_select(self, _evt=None):
        n = len(self.tree.selection())
        self.btn_del.config(state="normal" if n else "disabled")
        self.btn_open.config(state="normal" if n else "disabled")

    def _open_folder(self):
        for iid in self.tree.selection():
            r = self._rows.get(iid)
            if not r or r["cat"] in ("注册表残留",):
                continue
            p = r["target"]
            folder = p if os.path.isdir(p) else os.path.dirname(p)
            if os.path.isdir(folder):
                os.startfile(folder)  # noqa
            break

    def _delete_selected(self):
        sel = [self._rows[i] for i in self.tree.selection()]
        if not sel:
            return
        total_size = sum(r["size"] or 0 for r in sel if r["size"])
        lines = []
        risks = [r for r in sel if r["risk"]]
        saves = [r for r in sel if r.get("saves")]
        for r in sel:
            sz = o.human(r["size"]) if r["size"] else ""
            lines.append(f"  [{r['cat']}] {r['target']}  {sz}")
        msg = ("确定把以下 " + str(len(sel)) + " 项移入回收站?\n"
               "(不直接删除,随时可从「回收站」还原)\n\n" + "\n".join(lines))
        if risks:
            msg += ("\n\n⚠️ 包含启发式候选(可能误判为残留的正常软件),"
                    "请务必逐项核对!")
        if saves:
            msg += "\n\n⚠️ 包含带存档/配置的目录!"
        if not messagebox.askyesno("移至回收站", msg, icon="warning"):
            return
        self._scanning = True
        self.btn_del.config(state="disabled")
        self.lbl_state.config(text="处理中… (HKLM 注册表项会弹 UAC,请点「是」)")
        threading.Thread(target=self._trash_worker, args=(sel,),
                         daemon=True).start()

    def _trash_worker(self, items):
        report = []
        for r in items:
            try:
                if r["cat"] == "注册表残留":
                    # 先备份 .reg 到回收站,再删除键
                    okb, msgb, meta = o.backup_reg_key(r["target"])
                    if not okb:
                        report.append((r["target"], False,
                                       f"备份失败,已取消删除: {msgb}"))
                        continue
                    ok, m = o.delete_registry_key(r["target"])
                    report.append((r["target"], ok,
                                   f"{m}(备份: {meta['moved_to']})"))
                elif os.path.exists(r["target"]):
                    ok, m, meta = o.move_to_trash(r["target"])
                    report.append((r["target"], ok, m))
                else:
                    report.append((r["target"], False, "路径不存在(可能已被移动)"))
            except OSError as e:
                report.append((r["target"], False, str(e)))
        self._queue.put(("delete_done", report))

    def _after_delete(self, report):
        self._scanning = False
        ok_n = sum(1 for _, ok, _ in report if ok)
        for iid, r in list(self._rows.items()):
            if any(r["target"] == t for t, ok, _ in report if ok):
                self.tree.delete(iid)
                del self._rows[iid]
        self._on_select()
        self._refresh_trash_badge()
        if ok_n == len(report):
            self.lbl_state.config(text=f"已移入回收站 {ok_n} 项 ✅ (可从回收站还原)")
            messagebox.showinfo("完成",
                                f"成功 {ok_n} 项已移入回收站\n\n"
                                "它们没有真正删除!如果误移,点「🗑 回收站」即可还原。\n"
                                "确认不要后,再到回收站里「永久删除」释放空间。")
        else:
            fails = [f"{t}: {m}" for t, ok, m in report if not ok]
            self.lbl_state.config(text=f"处理完成:{ok_n} 成功 / {len(report) - ok_n} 失败")
            messagebox.showwarning("部分失败",
                                   "成功 " + str(ok_n) + " 项,失败 "
                                   + str(len(report) - ok_n) + " 项:\n"
                                   + "\n".join(fails[:8]))

    # ---------- 回收站管理 ----------
    def _refresh_trash_badge(self):
        n = len(o.trash_items())
        self.btn_trash.config(text=f"🗑 回收站({n})")

    def _open_trash(self):
        win = tk.Toplevel(self)
        win.title("回收站 — 移入的项目可在此还原或永久删除")
        win.geometry("860x460")
        win.transient(self)
        cols = ("name", "orig", "kind", "ts")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for cid, (txt, w, anc) in {
            "name": ("名称", 220, "w"),
            "orig": ("原位置 / 注册表键", 420, "w"),
            "kind": ("类型", 70, "center"),
            "ts": ("时间", 110, "center"),
        }.items():
            tree.heading(cid, text=txt)
            tree.column(cid, width=w, anchor=anc)
        vs = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        vs.grid(row=0, column=1, sticky="ns", pady=10)
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)

        items = o.trash_items()
        meta_by_iid = {}
        for it in items:
            kindmap = {"dir": "目录", "file": "文件", "reg": "注册表"}
            iid = tree.insert("", "end", values=(
                it.get("name", "?"),
                it.get("orig", it.get("moved_to", "?")),
                kindmap.get(it.get("kind"), it.get("kind", "?")),
                it.get("ts", "")))
            meta_by_iid[iid] = it

        bar = ttk.Frame(win, padding=(10, 0, 10, 10))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        lbl = ttk.Label(bar, text="", style="Hint.TLabel")
        lbl.pack(side="left")
        # 异步统计总大小
        def _calc_size():
            sz = o.human(o.trash_total_size())
            lbl.config(text=f"回收站共 {len(items)} 项,占用约 {sz}")
        threading.Thread(target=_calc_size, daemon=True).start()

        def _do_restore():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("提示", "先选中要还原的项目", parent=win)
                return
            for iid in sel:
                ok, m = o.restore_item(meta_by_iid[iid])
                messagebox.showinfo("还原", m, parent=win)
            win.destroy()
            self._refresh_trash_badge()
            self.lbl_state.config(text="已还原 — 可重新扫描查看")

        def _do_purge():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("提示", "先选中要永久删除的项目", parent=win)
                return
            if not messagebox.askyesno(
                    "永久删除",
                    f"确定永久删除选中的 {len(sel)} 项?\n"
                    "此操作不可恢复,将真正释放空间!", icon="warning",
                    parent=win):
                return
            for iid in sel:
                o.purge_item(meta_by_iid[iid])
            win.destroy()
            self._refresh_trash_badge()

        def _do_empty():
            if not messagebox.askyesno(
                    "清空回收站",
                    "确定清空回收站全部内容?\n此操作不可恢复!", icon="warning",
                    parent=win):
                return
            o.empty_trash()
            win.destroy()
            self._refresh_trash_badge()
            self.lbl_state.config(text="回收站已清空")

        ttk.Button(bar, text="↩ 还原选中项", command=_do_restore).pack(side="left")
        ttk.Button(bar, text="✖ 永久删除选中项",
                   command=_do_purge).pack(side="left", padx=6)
        ttk.Button(bar, text="清空全部", command=_do_empty).pack(side="left")

        def _open_loc():
            sel = tree.selection()
            if not sel:
                return
            p = meta_by_iid[sel[0]].get("moved_to")
            if p and os.path.exists(p):
                os.startfile(os.path.dirname(p))  # noqa
        ttk.Button(bar, text="📂 打开位置", command=_open_loc).pack(
            side="left", padx=6)

    # ---------- 反馈 ----------
    def _feedback(self):
        win = tk.Toplevel(self)
        win.title("反馈问题")
        win.geometry("640x480")
        win.transient(self)
        txt = o.system_diag()
        box = tk.Text(win, wrap="none", font=("Consolas", 10))
        box.insert("1.0", txt)
        box.config(state="disabled")
        vs = ttk.Scrollbar(win, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=vs.set)
        box.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 4))
        vs.pack(side="right", fill="y", pady=(10, 4))

        def _copy():
            self.clipboard_clear()
            self.clipboard_append(txt)
            messagebox.showinfo("已复制",
                                "诊断信息已复制到剪贴板", parent=win)

        def _open_log():
            try:
                os.startfile(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "orphan_scanner_errors.log"))  # noqa
            except OSError as e:
                messagebox.showinfo("提示", str(e), parent=win)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar, text="📋 复制诊断信息", command=_copy).pack(side="left")
        ttk.Button(bar, text="📄 打开错误日志", command=_open_log).pack(
            side="left", padx=6)
        ttk.Label(
            bar,
            text=("遇到问题?点击复制后,\n发到 GitHub Issues / 开发者。"
                  if not o.FEEDBACK_URL else
                  f"反馈地址: {o.FEEDBACK_URL}"),
            style="Hint.TLabel").pack(side="right")


if __name__ == "__main__":
    try:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    app = ScannerApp()
    app.mainloop()
