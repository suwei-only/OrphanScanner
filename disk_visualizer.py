# -*- coding: utf-8 -*-
"""
DiskMap —— 磁盘空间可视化引擎(SpaceSniffer 风格全量嵌套 treemap)
====================================================================
v3 核心:
- build_tree(): 一次性深度优先遍历整棵树(每个文件只统计一次,O(N)),
  自底向上汇总目录大小;大文件单独建节点,小文件聚合成一个块
- 渐进式渲染:扫描过程中共享树实时更新,UI 定时重绘,边扫边看
- 嵌套 treemap:所有目录/文件层层嵌套直接铺满视图(无需逐层下钻)
- 交互:单击选中 / 双击聚焦 / 右键删除(可还原回收站)/ 返回栈 / 停止扫描

兼容保留: measure / squarify / scan_items / scan_children_once / human / color_for
"""
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 文件类型配色
EXT_COLORS = {
    "dir": "#1E88E5",
    "video": "#F48FB1", "image": "#FFCC80", "audio": "#CE93D8",
    "doc": "#A5D6A7", "archive": "#B0BEC5", "code": "#90CAF9",
    "exe": "#EF9A9A", "other": "#CFD8DC",
}
EXT_MAP = {
    "mp4": "video", "mkv": "video", "avi": "video", "mov": "video",
    "flv": "video", "wmv": "video", "webm": "video", "ts": "video",
    "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
    "bmp": "image", "webp": "image", "heic": "image", "raw": "image",
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
DIR_PALETTE = ("#64B5F6", "#42A5F5", "#1E88E5", "#1976D2", "#1565C0")
# 聚合小块/进行中的颜色
AGG_COLOR = "#ECEFF1"
BUSY_COLOR = "#E0E0E0"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def color_for(name: str, is_dir: bool, depth: int = 0) -> str:
    if is_dir:
        return DIR_PALETTE[min(depth, len(DIR_PALETTE) - 1)]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EXT_COLORS.get(EXT_MAP.get(ext, "other"), EXT_COLORS["other"])


def measure(path: str, stop=None) -> int:
    """递归统计文件/目录大小(字节)"""
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


def scan_children_once(path: str, stop=None):
    """(兼容保留)扫描一层: [(name, full_path, is_dir, size)]"""
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if stop and stop.is_set():
                    break
                try:
                    if e.is_symlink() or e.name == ".OrphanTrash":
                        continue
                    is_dir = e.is_dir(follow_symlinks=False)
                    out.append((e.name, e.path, is_dir, measure(e.path, stop)))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def scan_items(path: str, on_item, on_done, stop=None, top_n: int = 400):
    """(兼容保留)逐项统计一层大小"""
    results = []
    for name, full, is_dir, size in scan_children_once(path, stop):
        results.append((name, full, size, is_dir))
        on_item(name, full, size, is_dir)
    on_done(len(results), sum(r[2] for r in results))


def squarify(items, x, y, w, h):
    """标准 squarified treemap。items: [(key, value)] -> [(key, x, y, w, h)]"""
    items = [(k, float(v)) for k, v in items if v and v > 0]
    if not items:
        return []
    items.sort(key=lambda t: -t[1])
    total = sum(v for _, v in items)
    rects = []
    scale = (w * h) / total
    scaled = [(k, v * scale) for k, v in items]

    def worst(row, side):
        if not row or side == 0:
            return float("inf")
        s = sum(v for _, v in row)
        mx = max(v for _, v in row)
        mn = min(v for _, v in row)
        if s == 0 or mn == 0:
            return float("inf")
        return max((side * side * mx) / (s * s),
                   (s * s) / (side * side * mn))

    while scaled:
        vertical = w >= h
        side = max(min(w, h), 1)
        row, best, rest = [], float("inf"), scaled[:]
        while rest:
            candidate = row + [rest[0]]
            cur = worst(candidate, side)
            if cur <= best:
                row = candidate
                best = cur
                rest.pop(0)
            else:
                break
        if not row:
            row = [rest.pop(0)]
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


# ==================== 树节点 ====================
class Node:
    __slots__ = ("name", "path", "size", "is_dir", "children",
                 "scanned", "parent", "depth", "agg")

    def __init__(self, name, path, size, is_dir, parent=None, agg=False):
        self.name = name
        self.path = path
        self.size = size
        self.is_dir = is_dir
        self.children = []
        self.scanned = False        # 目录子项是否扫描完成
        self.parent = parent
        self.depth = (parent.depth + 1) if parent else 0
        self.agg = agg              # 是否是"聚合小文件"伪节点


# ==================== 一次性建树(SpaceSniffer 核心) ====================
class TreeBuilder:
    """深度优先一次性遍历,自底向上汇总大小。
    - 目录节点全建(深度上限内)
    - 大文件(size >= big_file)单独建节点
    - 小文件聚合为一个伪节点(每个目录一个)
    - 预算保护:max_files 达到后停止深入
    """

    def __init__(self, big_file=5 * 1024 * 1024, max_files=800_000,
                 max_depth=12):
        self.big_file = big_file
        self.max_files = max_files
        self.max_depth = max_depth
        self.files_seen = 0
        self.dirs_seen = 0
        self.truncated = False
        self.current = ""

    def build(self, root_path, stop=None):
        name = os.path.basename(root_path.rstrip("\\/")) or root_path
        root = Node(name, root_path, 0, True, None)
        self._walk(root, stop)
        return root

    def _walk(self, node, stop):
        if stop is not None and stop.is_set():
            node.scanned = True
            return
        if self.files_seen >= self.max_files:
            self.truncated = True
            node.scanned = True
            return
        node.scanned = False
        self.current = node.path
        shallow = node.depth >= self.max_depth
        total = 0
        small_sum, small_cnt = 0, 0
        try:
            with os.scandir(node.path) as it:
                for e in it:
                    if stop is not None and stop.is_set():
                        break
                    if self.files_seen >= self.max_files:
                        self.truncated = True
                        break
                    try:
                        if e.is_symlink() or e.name == ".OrphanTrash":
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if shallow:
                                sz = measure(e.path, stop)
                                total += sz
                                node.children.append(
                                    Node(e.name, e.path, sz, True, node))
                            else:
                                child = Node(e.name, e.path, 0, True, node)
                                self._walk(child, stop)
                                total += child.size
                                node.children.append(child)
                        else:
                            st = e.stat(follow_symlinks=False)
                            self.files_seen += 1
                            total += st.st_size
                            if st.st_size >= self.big_file:
                                node.children.append(
                                    Node(e.name, e.path, st.st_size, False,
                                         node))
                            else:
                                small_sum += st.st_size
                                small_cnt += 1
                    except OSError:
                        continue
        except OSError:
            pass
        if small_cnt:
            agg = Node(f"({small_cnt}个小文件)", None, small_sum, False,
                       node, agg=True)
            node.children.append(agg)
        node.size = total
        node.scanned = True
        self.dirs_seen += 1


class DiskMapWindow(tk.Toplevel):
    """SpaceSniffer 风格全量嵌套磁盘地图"""

    MIN_PX = 16                      # 小于此像素不再细分
    MAX_BLOCKS = 4000                # 单次渲染最大块数

    def __init__(self, master, on_trash=None):
        super().__init__(master)
        self.title("📊 磁盘地图 — 全量空间可视化(双击聚焦 · 右键删除)")
        self.geometry("1180x760")
        self.minsize(900, 560)
        self.on_trash = on_trash
        self._stop = threading.Event()
        self._worker = None
        self._show_root = None
        self._focus = None
        self._stack = []
        self._pick = None
        self._rect_nodes = []
        self._blocks = 0
        self._builder = None
        self._build_ui()
        self.after(150, self._tick)

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="位置:").pack(side="left")
        self.cmb = ttk.Combobox(top, width=12, values=[
            "C:\\", "D:\\", "E:\\", "F:\\"], state="readonly")
        self.cmb.set("D:\\")
        self.cmb.pack(side="left", padx=4)
        ttk.Button(top, text="选择文件夹…", command=self._pick_dir).pack(side="left")
        ttk.Button(top, text="🔍 开始分析", command=self._start).pack(
            side="left", padx=6)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y",
                                                   padx=6, pady=2)
        self.btn_back = ttk.Button(top, text="⬅ 返回", command=self._back,
                                   state="disabled")
        self.btn_back.pack(side="left")
        self.btn_up = ttk.Button(top, text="🏠 根目录", command=self._home,
                                 state="disabled")
        self.btn_up.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(top, text="⏹ 停止扫描", command=self._stop_scan,
                                   state="disabled")
        self.btn_stop.pack(side="left")

        info = ttk.Frame(self, padding=(10, 0))
        info.pack(fill="x")
        self.lbl_path = ttk.Label(info, text="选择盘符后点「开始分析」——"
                                            "扫描中边算边画,几分钟内全盘文件一览无余",
                                  style="Hint.TLabel")
        self.lbl_path.pack(side="left")
        self.lbl_stat = ttk.Label(info, text="", style="Hint.TLabel")
        self.lbl_stat.pack(side="right")

        wrap = ttk.Frame(self, padding=(10, 4, 10, 4))
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#F5F5F5", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Configure>", lambda e: self._render())

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="♻ 移至回收站(可还原)",
                              command=self._trash_pick)
        self.menu.add_command(label="📂 打开所在文件夹", command=self._open_pick)
        self.menu.add_command(label="🔍 聚焦此目录", command=self._focus_pick)

        foot = ttk.Frame(self, padding=(10, 0, 10, 6))
        foot.pack(fill="x")
        ttk.Label(foot, text="单击选中 · 双击聚焦 · 右键删除(进回收站可还原)"
                             "· 小文件自动聚合成块",
                  style="Hint.TLabel").pack(side="left")
        self.lbl_sel = ttk.Label(foot, text="", style="Hint.TLabel")
        self.lbl_sel.pack(side="right")

    # ---------- 扫描 ----------
    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择要分析的文件夹")
        if d:
            self.cmb.set("")
            self._start(os.path.normpath(d))

    def _start(self, path=None):
        if path is None:
            path = self.cmb.get() or "D:\\"
        self._stop_scan(silent=True)
        self._root_path = path
        self._builder = TreeBuilder()
        self._show_root = Node(os.path.basename(path.rstrip("\\/")) or path,
                               path, 0, True, None)
        self._focus = self._show_root
        self._stack = []
        self._pick = None
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._scan_worker, daemon=True)
        self._worker.start()
        self.btn_stop.config(state="normal")
        self.btn_back.config(state="disabled")
        self.btn_up.config(state="normal")
        self.lbl_stat.config(text="扫描中…")
        self._render()

    def _scan_worker(self):
        """一次性建树,结果直接放到 self._show_root(共享对象,UI 定时读)"""
        stop = self._stop
        builder = self._builder
        built = builder.build(self._show_root.path, stop)
        # 建成后,把 built 的内容"接管"到 _show_root(保持 UI 引用不变)
        self._show_root.name = built.name
        self._show_root.size = built.size
        self._show_root.children = built.children
        self._show_root.scanned = built.scanned
        for c in self._show_root.children:
            c.parent = self._show_root
        self._done_flag = True

    def _stop_scan(self, silent=False):
        self._stop.set()
        self.btn_stop.config(state="disabled")
        if not silent:
            self.lbl_stat.config(text="已停止(显示已扫描部分)")

    def _tick(self):
        """UI 定时:重绘 + 状态栏(扫描中显示进度)"""
        if self._worker and self._worker.is_alive():
            b = self._builder
            self.lbl_stat.config(
                text=f"扫描中… {b.dirs_seen} 个目录 / {b.files_seen} 个文件"
                     f"  {b.current[-60:] if b.current else ''}")
            self._render()
        elif self._builder and not getattr(self, "_done_flag", False):
            # 完成后最后一帧
            f = self._builder.files_seen
            extra = "  ⚠ 达到扫描上限(仅显示部分)" if self._builder.truncated else ""
            self.lbl_stat.config(
                text=f"完成: {self._builder.dirs_seen} 个目录 / {f} 个文件"
                     f"{extra}")
            self._done_flag = True
            self._render()
        self.after(400 if (self._worker and self._worker.is_alive()) else 1500,
                   self._tick)

    # ---------- 渲染 ----------
    def _render(self):
        c = self.canvas
        c.delete("all")
        self._rect_nodes = []
        self._blocks = 0
        w, h = c.winfo_width(), c.winfo_height()
        if w < 60 or h < 60 or not self._focus:
            return
        node = self._focus
        self.lbl_path.config(text=f"📁 {node.path}  ({human(node.size)})")

        def draw(n, x, y, ww, hh, depth):
            if self._blocks >= self.MAX_BLOCKS:
                return
            try:
                kids = [k for k in list(n.children) if k.size > 0]
            except Exception:
                kids = []
            if not kids:
                if n.scanned:
                    self._draw_block(n, x, y, ww, hh, depth, leaf=True)
                else:
                    # 进行中:空目录或尚未读到子项
                    self._draw_block(n, x, y, ww, hh, depth, leaf="busy")
                return
            if ww < self.MIN_PX or hh < self.MIN_PX or depth > 30:
                self._draw_block(n, x, y, ww, hh, depth, leaf=True)
                return
            kids.sort(key=lambda k: -k.size)
            rects = squarify([(k, k.size) for k in kids], x, y, ww, hh)
            for k, cx, cy, cw, ch in rects:
                if self._blocks >= self.MAX_BLOCKS:
                    break
                self._blocks += 1
                can_split = (k.is_dir and not k.agg and k.scanned
                             and cw > 30 and ch > 30)
                if can_split:
                    self._draw_block(k, cx, cy, cw, ch, depth, leaf=False)
                    pad = 2
                    draw(k, cx + pad, cy + pad + 13, cw - 2 * pad,
                         ch - 2 * pad - 13, depth + 1)
                else:
                    self._draw_block(k, cx, cy, cw, ch, depth, leaf=True)

        draw(node, 3, 3, w - 6, h - 6, 0)
        # 进行中的顶部提示
        if self._worker and self._worker.is_alive():
            c.create_rectangle(w - 190, 6, w - 8, 30, fill="#FFF3E0",
                               outline="#FFB74D")
            c.create_text(w - 99, 18, text="⚙ 扫描中,布局持续细化…",
                          fill="#E65100", font=("Microsoft YaHei UI", 9))

    def _draw_block(self, n, x, y, w, h, depth, leaf):
        c = self.canvas
        if leaf == "busy":
            col, tc, tag = BUSY_COLOR, "#9E9E9E", "扫描中…"
        elif leaf and n.agg:
            col, tc, tag = AGG_COLOR, "#78909C", n.name
        elif leaf:
            col = color_for(n.name, n.is_dir, depth)
            tc = "#FFFFFF" if n.is_dir else "#102027"
            tag = n.name
        else:
            col = DIR_PALETTE[min(depth + 1, len(DIR_PALETTE) - 1)]
            tc, tag = "#FFFFFF", n.name
        sel = (n is self._pick)
        outline = "#FF6F00" if sel else "#FFFFFF"
        c.create_rectangle(x, y, x + w, y + h, fill=col, outline=outline,
                           width=3 if sel else 1)
        self._rect_nodes.append((n, x, y, w, h))
        if w > 42 and h > 16:
            label = tag if len(tag) <= 16 else tag[:14] + "…"
            c.create_text(x + 4, y + 3, text=label, anchor="nw", fill=tc,
                          font=("Microsoft YaHei UI", 9 if w > 80 else 8))
            if h > 34 and n.size:
                c.create_text(x + 4, y + 17, anchor="nw", text=human(n.size),
                              fill=tc if leaf != "busy" else "#BDBDBD",
                              font=("Microsoft YaHei UI", 8))

    # ---------- 交互 ----------
    def _hit(self, x, y):
        for n, rx, ry, rw, rh in reversed(self._rect_nodes):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return n
        return None

    def _on_click(self, ev):
        n = self._hit(ev.x, ev.y)
        self._pick = n
        self._render()
        if n:
            extra = "(聚合块,不可单独删除)" if n.agg else ""
            self.lbl_sel.config(text=f"选中: {n.path or n.name} "
                                     f"({human(n.size)}) {extra}")
        else:
            self.lbl_sel.config(text="")

    def _on_double(self, ev):
        n = self._hit(ev.x, ev.y)
        if n is None:
            return
        if n.is_dir:
            self._focus_node(n)
        elif n.path:
            os.startfile(os.path.dirname(n.path))  # noqa

    def _focus_node(self, n):
        if self._focus is not None and n is not self._focus:
            self._stack.append(self._focus)
        self._focus = n
        self.btn_back.config(state="normal" if self._stack else "disabled")
        self._render()

    def _on_right(self, ev):
        n = self._hit(ev.x, ev.y)
        if n is not None:
            self._pick = n
            self._render()
            self.menu.tk_popup(ev.x_root, ev.y_root)

    def _back(self):
        if self._stack:
            self._focus = self._stack.pop()
            self.btn_back.config(state="normal" if self._stack else "disabled")
            self._render()

    def _home(self):
        self._stack = []
        self._focus = self._show_root
        self.btn_back.config(state="disabled")
        self._render()

    def _focus_pick(self):
        if self._pick and self._pick.is_dir:
            self._focus_node(self._pick)

    def _open_pick(self):
        if not self._pick or not self._pick.path:
            return
        target = (self._pick.path if self._pick.is_dir
                  else os.path.dirname(self._pick.path))
        os.startfile(target)  # noqa

    def _trash_pick(self):
        n = self._pick
        if not n or not n.path:
            if n and n.agg:
                messagebox.showinfo("提示", "聚合块包含多个小文件,"
                                            "请聚焦到目录后删除具体文件",
                                    parent=self)
            return
        if n is self._show_root:
            messagebox.showinfo("提示", "这是当前根目录,不能在这里删除",
                                parent=self)
            return
        if not messagebox.askyesno(
                "移至回收站",
                f"把以下项目移到回收站(可还原)?\n\n{n.path}\n大小: {human(n.size)}",
                icon="warning", parent=self):
            return
        if self.on_trash:
            ok, msg = self.on_trash(n.path)
        else:
            try:
                import orphan_scanner as o
                ok, msg, _meta = o.move_to_trash(n.path)
            except Exception as e:
                ok, msg = False, str(e)
        if ok:
            p = n.parent
            while p is not None:
                p.size = max(0, p.size - n.size)
                if n in p.children:
                    p.children.remove(n)
                p = p.parent
            self._pick = None
            self.lbl_sel.config(text=f"已移入回收站(可还原): {n.name}")
            self._render()
        else:
            messagebox.showerror("失败", msg, parent=self)
