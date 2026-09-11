# -*- coding: utf-8 -*-
"""
DiskMap —— 磁盘空间可视化引擎(SpaceSniffer 风格 squarified treemap)
=====================================================================
- scan_items(path, on_item, stop):后台逐项统计目录大小(渐进式)
- squarify(items, x, y, w, h):标准 squarified treemap 布局算法
- DiskMapWindow:tkinter Canvas 可视化窗口,支持下钻/返回/右键删除
"""
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 文件类型配色(浅色系,配深色文字)
EXT_COLORS = {
    "dir": ("#1E88E5", "#1565C0"),        # 目录:蓝色系
    "video": "#F48FB1", "image": "#FFCC80", "audio": "#CE93D8",
    "doc": "#A5D6A7", "archive": "#B0BEC5", "code": "#90CAF9",
    "exe": "#EF9A9A", "other": "#CFD8DC",
}
EXT_MAP = {
    "mp4": "video", "mkv": "video", "avi": "video", "mov": "video",
    "flv": "video", "wmv": "video", "webm": "video",
    "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
    "bmp": "image", "webp": "image", "heic": "image",
    "mp3": "audio", "wav": "audio", "flac": "audio", "aac": "audio",
    "m4a": "audio", "ogg": "audio",
    "doc": "doc", "docx": "doc", "pdf": "doc", "txt": "doc",
    "xls": "doc", "xlsx": "doc", "ppt": "doc", "pptx": "doc",
    "zip": "archive", "rar": "archive", "7z": "archive",
    "tar": "archive", "gz": "archive", "xz": "archive",
    "py": "code", "c": "code", "cpp": "code", "h": "code",
    "js": "code", "ts": "code", "java": "code", "cs": "code",
    "exe": "exe", "msi": "exe", "dll": "exe", "sys": "exe",
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def color_for(name: str, is_dir: bool, depth: int = 0) -> str:
    if is_dir:
        palette = ("#42A5F5", "#1E88E5", "#1976D2", "#1565C0")
        return palette[min(depth, 3)]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EXT_COLORS.get(EXT_MAP.get(ext, "other"), EXT_COLORS["other"])


def measure(path: str, stop=None) -> int:
    """递归统计文件/目录大小(字节);目录不可读时返回已统计部分"""
    try:
        st = os.stat(path, follow_symlinks=False)
        if not os.path.isdir(path):
            return st.st_size
    except OSError:
        return 0
    total = 0
    stack = [path]
    while stack:
        if stop and stop.is_set():
            return total
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if stop and stop.is_set():
                        return total
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def scan_items(path: str, on_item, on_done, stop=None, top_n: int = 400):
    """后台逐项统计 path 直接子项的大小(按完成顺序回调 on_item)
    on_item(name, full_path, size, is_dir)
    on_done(total_count, total_size)
    """
    try:
        entries = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    is_dir = e.is_dir(follow_symlinks=False)
                    # 跳过回收站自己的目录,避免自引用
                    if is_dir and e.name == ".OrphanTrash":
                        continue
                    entries.append((e.name, e.path, is_dir))
                except OSError:
                    continue
        # 先文件后目录不重要;统计
        results = []
        for name, full, is_dir in entries:
            if stop and stop.is_set():
                break
            size = measure(full, stop)
            results.append((name, full, size, is_dir))
            on_item(name, full, size, is_dir)
        # 数量太多时只保留 top_n?(回调已发出,由 UI 决定显示)
        on_done(len(results), sum(r[2] for r in results))
    except OSError as e:
        on_done(0, 0)


def squarify(items, x, y, w, h):
    """标准 squarified treemap。
    items: [(key, value)] value>0,自动按降序处理
    返回: [(key, x, y, w, h)]
    """
    items = [(k, float(v)) for k, v in items if v and v > 0]
    if not items:
        return []
    items.sort(key=lambda t: -t[1])
    total = sum(v for _, v in items)
    rects = []

    def layout_row(row, x, y, w, h, vertical):
        """把一行(row)铺进当前矩形,返回剩余矩形"""
        row_sum = sum(v for _, v in row)
        if row_sum <= 0:
            return x, y, w, h
        if vertical:
            # 竖着切:行占左侧一条
            rw = row_sum  # 已经按面积缩放
            cy = y
            for k, v in row:
                rh = h * (v / row_sum)
                rects.append((k, x, cy, rw, rh))
                cy += rh
            return x + rw, y, w - rw, h
        else:
            rh = row_sum
            cx = x
            for k, v in row:
                rw2 = w * (v / row_sum)
                rects.append((k, cx, y, rw2, rh))
                cx += rw2
            return x, y + rh, w, h - rh

    # 面积缩放:总像素面积 / 总面积
    scale = (w * h) / total
    scaled = [(k, v * scale) for k, v in items]

    def worst(row, side):
        if not row:
            return float("inf")
        s = sum(v for _, v in row)
        mx = max(v for _, v in row)
        mn = min(v for _, v in row)
        if s == 0 or side == 0:
            return float("inf")
        return max((side * side * mx) / (s * s),
                   (s * s) / (side * side * mn))

    while scaled:
        vertical = w >= h
        side = w if vertical else h     # 当前短边用于评价
        row = []
        best = float("inf")
        rest = scaled[:]
        while rest:
            candidate = row + [rest[0]]
            cur = worst(candidate, max(min(w, h), 1))
            if cur <= best:
                row = candidate
                best = cur
                rest.pop(0)
            else:
                break
        if not row:   # 保险
            row = [rest.pop(0)]
        # 放置这一行
        row_sum = sum(v for _, v in row)
        if vertical:
            rw = row_sum / h if h else 0
            cy = y
            for k, v in row:
                rh = h * (v / row_sum)
                rects.append((k, x, cy, max(rw, 1), rh))
                cy += rh
            w -= rw
            x += rw
        else:
            rh = row_sum / w if w else 0
            cx = x
            for k, v in row:
                rw2 = w * (v / row_sum)
                rects.append((k, cx, y, rw2, max(rh, 1)))
                cx += rw2
            h -= rh
            y += rh
        scaled = rest
    return rects


class DiskMapWindow(tk.Toplevel):
    """磁盘空间可视化窗口"""

    def __init__(self, master, on_trash=None):
        super().__init__(master)
        self.title("📊 磁盘地图 — 空间可视化(右键可删除)")
        self.geometry("1120x700")
        self.minsize(860, 520)
        self.on_trash = on_trash          # 删除回调(复用回收站);None 则内部处理
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._items = []                  # [(name, path, size, is_dir)]
        self._rects = []                  # [(item, x, y, w, h)]
        self._stack = []                  # 父路径栈
        self._current = ""
        self._selected = None
        self._scanning = False
        self._build_ui()
        self.after(100, self._drain)

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="位置:").pack(side="left")
        self.cmb = ttk.Combobox(top, width=14, values=[
            "C:\\", "D:\\", "E:\\", "F:\\"], state="readonly")
        self.cmb.set("D:\\")
        self.cmb.pack(side="left", padx=4)
        ttk.Button(top, text="选择文件夹…", command=self._pick).pack(side="left")
        ttk.Button(top, text="🔍 开始分析", command=self._start).pack(
            side="left", padx=6)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y",
                                                   padx=6, pady=2)
        self.btn_back = ttk.Button(top, text="⬅ 返回上级", command=self._back,
                                   state="disabled")
        self.btn_back.pack(side="left")
        self.btn_up = ttk.Button(top, text="🏠 根目录", command=self._home,
                                 state="disabled")
        self.btn_up.pack(side="left", padx=4)
        self.btn_rescan = ttk.Button(top, text="⟳ 重新扫描",
                                     command=self._rescan, state="disabled")
        self.btn_rescan.pack(side="left")

        # 面包屑 + 状态
        info = ttk.Frame(self, padding=(10, 0))
        info.pack(fill="x")
        self.lbl_path = ttk.Label(info, text="请选择盘符并点击「开始分析」",
                                  style="Hint.TLabel")
        self.lbl_path.pack(side="left")
        self.lbl_stat = ttk.Label(info, text="", style="Hint.TLabel")
        self.lbl_stat.pack(side="right")

        # 画布
        wrap = ttk.Frame(self, padding=(10, 4, 10, 8))
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#FAFAFA", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Configure>", lambda e: self._redraw())

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="♻ 移至回收站(可还原)",
                              command=self._trash_selected)
        self.menu.add_command(label="📂 打开所在文件夹",
                              command=self._open_selected)
        self.menu.add_command(label="⬇ 进入此目录", command=self._enter_selected)

        # 底部提示
        ttk.Label(self, text="单击选中 · 双击目录进入 · 右键删除/打开 · "
                             "删除进回收站可还原",
                  style="Hint.TLabel", padding=(10, 0, 10, 6)).pack(fill="x")

    # ---------- 扫描流程 ----------
    def _pick(self):
        d = filedialog.askdirectory(title="选择要分析的文件夹")
        if d:
            self.cmb.set("")
            self._root_path = os.path.normpath(d)
            self._start(self._root_path)

    def _start(self, path=None):
        if self._scanning:
            return
        if path is None:
            path = self.cmb.get() or "D:\\"
        self._root_path = path
        self._stack = []
        self._load(path)

    def _rescan(self):
        if self._current and not self._scanning:
            self._load(self._current)

    def _load(self, path):
        self._stop.set()
        self._stop = threading.Event()
        self._scanning = True
        self._current = path
        self._items = []
        self._selected = None
        self.canvas.delete("all")
        self.lbl_path.config(text=f"📁 {path}")
        self.lbl_stat.config(text="分析中…")
        self.btn_back.config(state="normal" if self._stack else "disabled")
        self.btn_up.config(state="normal")
        self.btn_rescan.config(state="disabled")
        stop = self._stop
        q = self._queue

        def worker():
            def on_item(name, full, size, is_dir):
                q.put(("item", (name, full, size, is_dir)))
            scan_items(path, on_item, lambda n, s: q.put(("done", (n, s))), stop)

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        redraw = False
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "item":
                    self._items.append(payload)
                    redraw = True
                elif kind == "done":
                    n, s = payload
                    self._scanning = False
                    self.btn_rescan.config(state="normal")
                    self.lbl_stat.config(
                        text=f"{n} 个项目,共 {human(s)}")
                    redraw = True
        except queue.Empty:
            pass
        if redraw:
            self._redraw()
        self.after(150, self._drain)

    # ---------- 绘制 ----------
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50 or not self._items:
            if not self._items and not self._scanning:
                c.create_text(w // 2, h // 2, text="(空目录或无权限)",
                              fill="#90A4AE", font=("Microsoft YaHei UI", 12))
            return
        items = [(i, r[2]) for i, r in enumerate(self._items) if r[2] > 0]
        items.sort(key=lambda t: -t[1])
        # 太大数量时合并小项为"其他"
        MAX_BLOCKS = 300
        if len(items) > MAX_BLOCKS:
            kept = items[:MAX_BLOCKS - 1]
            rest_sum = sum(v for _, v in items[MAX_BLOCKS - 1:])
            merged = kept + [(-1, rest_sum)]
        else:
            merged = items
        total = sum(v for _, v in merged) or 1
        rects = squarify([(k, v) for k, v in merged], 4, 4, w - 8, h - 8)
        self._rects = rects
        for key, rx, ry, rw, rh in rects:
            if key == -1:
                name, is_dir, full = "(其他小项)", False, ""
                col = "#ECEFF1"
            else:
                name, full, size, is_dir = self._items[key]
                col = color_for(name, is_dir, len(self._stack))
            sel = (self._selected == key)
            outline = "#FF6F00" if sel else "#FFFFFF"
            lw = 3 if sel else 1
            c.create_rectangle(rx, ry, rx + rw, ry + rh, fill=col,
                               outline=outline, width=lw, tags=("blk",))
            if rw > 46 and rh > 20:
                label = name if len(name) <= 18 else name[:16] + "…"
                fs = 9 if rw > 90 else 8
                c.create_text(rx + 5, ry + 4, text=label, anchor="nw",
                              fill="#102027" if not is_dir else "#FFFFFF",
                              font=("Microsoft YaHei UI", fs))
                if rh > 36:
                    c.create_text(rx + 5, ry + 20, anchor="nw",
                                  text=human(size),
                                  fill="#37474F" if not is_dir else "#E3F2FD",
                                  font=("Microsoft YaHei UI", 8))

    # ---------- 交互 ----------
    def _hit(self, x, y):
        for key, rx, ry, rw, rh in self._rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return key
        return None

    def _on_click(self, ev):
        key = self._hit(ev.x, ev.y)
        self._selected = key
        self._redraw()
        if key is not None and key != -1:
            name, full, size, is_dir = self._items[key]
            self.lbl_path.config(text=f"📁 {self._current}   ▸ 选中: {name}"
                                      f"({human(size)})")

    def _on_double(self, ev):
        key = self._hit(ev.x, ev.y)
        if key is None or key == -1:
            return
        name, full, size, is_dir = self._items[key]
        if is_dir:
            self._stack.append(self._current)
            self._load(full)
        else:
            os.startfile(os.path.dirname(full))  # noqa

    def _on_right(self, ev):
        key = self._hit(ev.x, ev.y)
        if key is not None:
            self._selected = key
            self._redraw()
            self.menu.tk_popup(ev.x_root, ev.y_root)

    def _back(self):
        if self._stack:
            prev = self._stack.pop()
            self._load(prev)

    def _home(self):
        self._stack = []
        self._load(self._root_path)

    def _enter_selected(self):
        if self._selected in (None, -1):
            return
        name, full, size, is_dir = self._items[self._selected]
        if is_dir:
            self._stack.append(self._current)
            self._load(full)

    def _open_selected(self):
        if self._selected in (None, -1):
            return
        name, full, size, is_dir = self._items[self._selected]
        target = full if is_dir else os.path.dirname(full)
        os.startfile(target)  # noqa

    def _trash_selected(self):
        if self._selected in (None, -1):
            return
        name, full, size, is_dir = self._items[self._selected]
        if not messagebox.askyesno(
                "移至回收站",
                f"把以下项目移到回收站(可还原)?\n\n{full}\n大小: {human(size)}",
                icon="warning", parent=self):
            return
        if self.on_trash:
            ok, msg = self.on_trash(full)
        else:
            try:
                import orphan_scanner as o
                ok, msg, _meta = o.move_to_trash(full)
            except Exception as e:
                ok, msg = False, str(e)
        if ok:
            # 从视图移除该项
            self._items = [it for it in self._items if it[1] != full]
            self._selected = None
            self.lbl_stat.config(text=f"已移入回收站: {name}(可还原)")
            self._redraw()
        else:
            messagebox.showerror("失败", msg, parent=self)
