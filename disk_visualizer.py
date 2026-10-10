# -*- coding: utf-8 -*-
"""
DiskMap —— 磁盘空间可视化引擎(SpaceSniffer/disktree 风格全量嵌套 treemap)
==========================================================================
v1.5 核心升级:
- TreeBuilder:NTFS 分配大小口径(可选)、硬链接文件ID去重、无权限目录跳过记录、
  每节点文件数/最后修改时间
- 交互对齐 disktree:悬停高亮+tooltip(路径/大小/占比/文件数)、单击目录下钻、
  面包屑返回+同级大目录横跳、滚轮缩放、右侧选中面板(大小/占比/文件数/修改时间/
  应用归属)、空格标记待删+复核清单(目录吸收内部标记、不可反向)、删除前预览
  释放空间、系统目录拒删(走 orphan_scanner.is_protected_path)

兼容保留: measure / squarify / scan_items / scan_children_once / human / color_for
"""
import os
import threading
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
AGG_COLOR = "#ECEFF1"
BUSY_COLOR = "#E0E0E0"
MARK_COLOR = "#B71C1C"          # 标记待删边框


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
                 "scanned", "parent", "depth", "agg",
                 "file_count", "mtime")

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
        self.file_count = 0         # 子树文件数(目录聚合;文件=1;agg=聚合数)
        self.mtime = 0.0            # 子树最后修改时间


def is_descendant(node, ancestor):
    """node 是否在 ancestor 子树内(含自身)"""
    n = node
    while n is not None:
        if n is ancestor:
            return True
        n = n.parent
    return False


def absorb_marked(marked, node):
    """标记目录:吸收(移除)其子树内已标记项,并把目录自身加入标记列表。
    不可反向——取消目录标记不会恢复被吸收的内部项。
    node 已在 marked 中则原样返回(幂等)。"""
    if node in marked:
        return marked
    return [m for m in marked if not is_descendant(m, node)] + [node]


# ==================== 一次性建树(SpaceSniffer 核心) ====================
class TreeBuilder:
    """深度优先一次性遍历,自底向上汇总大小。
    - 目录节点全建(深度上限内);大文件单独建节点;小文件聚合为伪节点
    - use_alloc=True:目录枚举走 NTFS 分配大小(GetFileInformationByHandleEx),
      失败自动退化 st_size 并在 alloc_ok=False 标注口径
    - dedupe_hardlinks:st_nlink>1 的文件按 (卷序列号, 文件ID) 去重,
      去重字节累计进 dup_saved
    - 无权限目录跳过不崩,记录进 skipped
    """

    def __init__(self, big_file=5 * 1024 * 1024, max_files=800_000,
                 max_depth=12, use_alloc=False, dedupe_hardlinks=True):
        self.big_file = big_file
        self.max_files = max_files
        self.max_depth = max_depth
        self.use_alloc = use_alloc
        self.dedupe = dedupe_hardlinks
        self.files_seen = 0
        self.dirs_seen = 0
        self.truncated = False
        self.current = ""
        self.skipped = []          # [(path, errno, msg)] 无权限跳过
        self.dup_saved = 0         # 硬链接去重字节
        self.alloc_ok = None       # None=未用alloc;True=分配大小口径;False=有退化表观大小
        self.alloc_misses = 0      # 分配大小获取失败的次数(口径标注用)
        self.seen_ids = set()

    def build(self, root_path, stop=None):
        name = os.path.basename(root_path.rstrip("\\/")) or root_path
        root = Node(name, root_path, 0, True, None)
        self._walk(root, stop)
        if self.use_alloc and self.files_seen > 0:
            # 口径结算:全部文件拿到分配大小 → True;有退化 → False(UI 标注)
            self.alloc_ok = (self.alloc_misses == 0)
        return root

    def _iter_dir(self, path):
        """目录枚举 → [(name, full_path, is_dir, st|None)]
        st 为文件 stat(表观大小兜底/mtime/nlink);目录为 None。
        权限错误记录 skipped。"""
        out = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_symlink() or e.name == ".OrphanTrash":
                            continue
                        is_dir = e.is_dir(follow_symlinks=False)
                        st = None if is_dir else e.stat(follow_symlinks=False)
                        out.append((e.name, e.path, is_dir, st))
                    except OSError as exc:
                        if len(self.skipped) < 5000:
                            self.skipped.append((e.path, exc.errno, str(exc)))
        except OSError as exc:
            if len(self.skipped) < 5000:
                self.skipped.append((path, exc.errno, str(exc)))
        return out

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
        entries = self._iter_dir(node.path)
        for name, e_path, is_dir, st in entries:
            if stop is not None and stop.is_set():
                break
            if self.files_seen >= self.max_files:
                self.truncated = True
                break
            try:
                if is_dir:
                    if shallow:
                        sz = measure(e_path, stop)
                        total += sz
                        child = Node(name, e_path, sz, True, node)
                        node.children.append(child)
                    else:
                        child = Node(name, e_path, 0, True, node)
                        self._walk(child, stop)
                        total += child.size
                        node.children.append(child)
                else:
                    self.files_seen += 1
                    sz = st.st_size
                    nlink = getattr(st, "st_nlink", 1)
                    if self.use_alloc:
                        # NTFS 分配大小优先(磁盘实际占用);失败退化 st_size
                        got = None
                        try:
                            from full_scan import file_alloc_links
                            got = file_alloc_links(e_path)
                        except Exception:
                            got = None
                        if got is not None and got[0] > 0:
                            sz = got[0]
                            nlink = got[1]
                        else:
                            self.alloc_misses += 1
                    dup = False
                    if self.dedupe and nlink > 1:
                        fid = None
                        try:
                            from full_scan import get_file_id
                            fid = get_file_id(e_path)
                        except Exception:
                            fid = None
                        if fid is not None:
                            if fid in self.seen_ids:
                                self.dup_saved += sz
                                dup = True
                            else:
                                self.seen_ids.add(fid)
                    if not dup:
                        total += sz
                        if st.st_mtime > node.mtime:
                            node.mtime = st.st_mtime
                        if sz >= self.big_file:
                            child = Node(name, e_path, sz, False, node)
                            child.file_count = 1
                            child.mtime = st.st_mtime
                            node.children.append(child)
                        else:
                            small_sum += sz
                            small_cnt += 1
            except OSError as exc:
                if len(self.skipped) < 5000:
                    self.skipped.append((e_path, exc.errno, str(exc)))
        if small_cnt:
            agg = Node(f"({small_cnt}个小文件)", None, small_sum, False,
                       node, agg=True)
            agg.file_count = small_cnt
            node.children.append(agg)
        node.size = total
        node.file_count = sum(c.file_count for c in node.children)
        node.mtime = max((c.mtime for c in node.children), default=node.mtime)
        node.scanned = True
        self.dirs_seen += 1


# ==================== 磁盘地图窗口 ====================
class DiskMapWindow(tk.Toplevel):
    """disktree 风格磁盘地图:悬停/下钻/面包屑/同级横跳/滚轮缩放/
    选中面板(归属识别)/空格标记待删+复核清单"""

    MAX_BLOCKS = 4000                # 单次渲染最大块数
    ZOOM_RANGE = (4, 64)             # MIN_PX 缩放范围

    def __init__(self, master, on_trash=None):
        super().__init__(master)
        self.title("📊 磁盘地图 — 全盘空间可视化 v1.5")
        self.geometry("1380x800")
        self.minsize(1080, 640)
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
        self._done_flag = False
        self._zoom = 16
        self._marked = []            # 标记待删节点列表
        self._hover = None
        self._motion_job = None
        self._owner_seq = 0          # 归属识别竞态防护
        self._build_ui()
        self.after(150, self._tick)

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self)
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground="#757575")
        style.configure("Big.TLabel", font=("Microsoft YaHei UI", 22, "bold"),
                        foreground="#1565C0")
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="扫描范围:").pack(side="left")
        self.vol_sel = ttk.Combobox(top, width=26, state="readonly")
        self.vol_sel.pack(side="left", padx=4)
        self.v_user = tk.BooleanVar(value=True)
        self.chk_user = ttk.Checkbutton(top, text="仅扫描用户目录",
                                        variable=self.v_user)
        self.chk_user.pack(side="left")
        ttk.Button(top, text="选择文件夹…", command=self._pick_dir).pack(
            side="left", padx=6)
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
        self.btn_stop = ttk.Button(top, text="⏹ 停止扫描",
                                   command=self._stop_scan, state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_skipped = ttk.Button(top, text="📋 跳过列表",
                                      command=self._show_skipped,
                                      state="disabled")
        self.btn_skipped.pack(side="left", padx=4)
        self._fill_volumes()

        # 面包屑
        crumb = ttk.Frame(self, padding=(10, 0))
        crumb.pack(fill="x")
        ttk.Label(crumb, text="路径:", style="Hint.TLabel").pack(side="left")
        self.crumb_bar = ttk.Frame(crumb)
        self.crumb_bar.pack(side="left", fill="x", expand=True)

        info = ttk.Frame(self, padding=(10, 0))
        info.pack(fill="x")
        self.lbl_stat = ttk.Label(info, text="", style="Hint.TLabel")
        self.lbl_stat.pack(side="right")

        mid = ttk.Frame(self, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(mid, bg="#F5F5F5", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.bind("<space>", self._toggle_mark)

        # ---- 右侧面板 ----
        panel = ttk.Frame(mid, padding=(8, 0, 0, 0), width=330)
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        ttk.Label(panel, text="选中项", font=("Microsoft YaHei UI", 11,
                                              "bold")).pack(anchor="w")
        self.lbl_name = ttk.Label(panel, text="—", wraplength=310)
        self.lbl_name.pack(anchor="w")
        self.lbl_size = ttk.Label(panel, text="", style="Big.TLabel")
        self.lbl_size.pack(anchor="w")
        self.lbl_pct = ttk.Label(panel, text="", style="Hint.TLabel")
        self.lbl_pct.pack(anchor="w")
        self.lbl_meta = ttk.Label(panel, text="", style="Hint.TLabel",
                                  wraplength=310, justify="left")
        self.lbl_meta.pack(anchor="w", pady=(2, 0))
        self.lbl_owner = ttk.Label(panel, text="", wraplength=310,
                                   justify="left",
                                   font=("Microsoft YaHei UI", 9, "bold"),
                                   foreground="#2E7D32")
        self.lbl_owner.pack(anchor="w", pady=(4, 0))
        self.btn_mark = ttk.Button(panel, text="🗑 标记待删(空格)",
                                   command=self._toggle_mark,
                                   state="disabled")
        self.btn_mark.pack(anchor="w", pady=(6, 2))

        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(panel, text="同级大目录(点此横跳)",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.sib_box = tk.Listbox(panel, height=8, font=("Microsoft YaHei UI",
                                                         9))
        self.sib_box.pack(fill="x")
        self.sib_box.bind("<Double-Button-1>", self._jump_sibling)
        self._sib_nodes = []

        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(panel, text="🗑 标记清单(待删)",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.mark_box = tk.Listbox(panel, height=6,
                                   font=("Microsoft YaHei UI", 9))
        self.mark_box.pack(fill="x")
        ttk.Button(panel, text="🔍 复核删除(预览释放空间)",
                   command=self._review_delete).pack(anchor="w", pady=4)
        self.lbl_release = ttk.Label(panel, text="", style="Hint.TLabel")
        self.lbl_release.pack(anchor="w")

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="♻ 移至回收站(可还原)",
                              command=self._trash_pick)
        self.menu.add_command(label="📂 打开所在文件夹", command=self._open_pick)
        self.menu.add_command(label="🔍 聚焦此目录", command=self._focus_pick)
        self.menu.add_command(label="🗑 标记/取消标记待删",
                              command=self._toggle_mark)

        foot = ttk.Frame(self, padding=(10, 0, 10, 6))
        foot.pack(fill="x")
        ttk.Label(foot, text="单击目录下钻 · 滚轮缩放 · 空格标记待删 · "
                             "双击打开文件夹 · 右键删除(进回收站可还原)",
                  style="Hint.TLabel").pack(side="left")
        self.lbl_sel = ttk.Label(foot, text="", style="Hint.TLabel")
        self.lbl_sel.pack(side="right")

    def _fill_volumes(self):
        try:
            from full_scan import list_volumes
            vols = list_volumes()
            home = os.path.expanduser("~")
            home_drive = os.path.splitdrive(home)[0][:1].upper()
            vals, default = [], None
            for letter, label, free, total in vols:
                lbl = label or ("系统" if letter == "C" else "本地磁盘")
                vals.append(f"{letter}: ({lbl}) 剩余 {human(free)}")
                if letter == home_drive:
                    default = vals[-1]
            self.vol_sel["values"] = vals
            self.vol_sel.set(default or (vals[0] if vals else "C:"))
        except Exception:
            self.vol_sel["values"] = ["C:", "D:", "E:", "F:"]
            self.vol_sel.set("C:")

    def _selected_drive(self):
        v = self.vol_sel.get()
        return (v.split(":", 1)[0] + ":") if ":" in v else "C:"

    # ---------- 扫描 ----------
    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择要分析的文件夹")
        if d:
            self.v_user.set(False)
            self._start(os.path.normpath(d))

    def _scan_root(self):
        drive = self._selected_drive()
        if self.v_user.get():
            home = os.path.expanduser("~")
            if os.path.splitdrive(home)[0][:1].upper() == drive:
                return os.path.normpath(home)
        return drive + "\\"

    def _start(self, path=None):
        if path is None:
            path = self._scan_root()
        self._stop_scan(silent=True)
        self._root_path = path
        self._builder = TreeBuilder(use_alloc=True, dedupe_hardlinks=True)
        self._show_root = Node(os.path.basename(path.rstrip("\\/")) or path,
                               path, 0, True, None)
        self._focus = self._show_root
        self._stack = []
        self._pick = None
        self._marked = []
        self._refresh_mark_list()
        self._stop = threading.Event()
        self._done_flag = False
        self._scan_error = None
        self._worker = threading.Thread(target=self._scan_worker, daemon=True)
        self._worker.start()
        self.btn_stop.config(state="normal")
        self.btn_back.config(state="disabled")
        self.btn_up.config(state="normal")
        self.lbl_stat.config(text="扫描中…")
        self._render()

    def _scan_worker(self):
        """一次性建树,结果直接放到 self._show_root(共享对象,UI 定时读)"""
        try:
            stop = self._stop
            builder = self._builder
            built = builder.build(self._show_root.path, stop)
            self._show_root.name = built.name
            self._show_root.size = built.size
            self._show_root.children = built.children
            self._show_root.scanned = built.scanned
            self._show_root.file_count = built.file_count
            self._show_root.mtime = built.mtime
            for c in self._show_root.children:
                c.parent = self._show_root
        except Exception as e:
            self._scan_error = str(e)
        self._done_flag = True

    def _stop_scan(self, silent=False):
        self._stop.set()
        self.btn_stop.config(state="disabled")
        if not silent:
            self.lbl_stat.config(text="已停止(显示已扫描部分)")

    def _tick(self):
        if self._worker and self._worker.is_alive():
            b = self._builder
            self.lbl_stat.config(
                text=f"扫描中… {b.dirs_seen} 个目录 / {b.files_seen} 个文件"
                     f"  {b.current[-50:] if b.current else ''}")
            self._render()
        elif self._builder and not self._done_flag:
            self._done_flag = True
            f = self._builder.files_seen
            extra = "  ⚠ 达到扫描上限(仅显示部分)" if self._builder.truncated \
                else ""
            if self._builder.alloc_ok is False:
                extra += " · ⚠ 大小口径:表观大小(分配大小不可用)"
            if self._builder.dup_saved:
                extra += (f" · 硬链接去重 {human(self._builder.dup_saved)}")
            self.lbl_stat.config(
                text=f"完成: {self._builder.dirs_seen} 个目录 / {f} 个文件"
                     f"{extra}")
            if getattr(self, "_scan_error", None):
                messagebox.showerror("扫描出错", self._scan_error, parent=self)
            if self._builder.skipped:
                self.lbl_sel.config(
                    text=f"⚠ 跳过 {len(self._builder.skipped)} 个无权限目录")
                self.btn_skipped.config(state="normal")
            self._render()
        self.after(400 if (self._worker and self._worker.is_alive())
                   else 1500, self._tick)

    def _show_skipped(self):
        b = self._builder
        if not b or not b.skipped:
            messagebox.showinfo("跳过列表", "没有跳过的目录", parent=self)
            return
        lines = [f"{p}  (错误 {e}: {m})" for p, e, m in b.skipped[:50]]
        if len(b.skipped) > 50:
            lines.append(f"… 共 {len(b.skipped)} 条")
        win = tk.Toplevel(self)
        win.title(f"无权限/跳过目录({len(b.skipped)})")
        win.geometry("820x480")
        win.transient(self)
        txt = tk.Text(win, wrap="none", font=("Consolas", 9))
        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")
        vs = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        txt.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        vs.pack(side="right", fill="y", pady=10)

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
        self._refresh_crumbs()

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
                    self._draw_block(n, x, y, ww, hh, depth, leaf="busy")
                return
            if ww < self._zoom or hh < self._zoom or depth > 30:
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
        marked = (n in self._marked)
        outline = MARK_COLOR if marked else ("#FF6F00" if sel else "#FFFFFF")
        width = 3 if (sel or marked) else 1
        c.create_rectangle(x, y, x + w, y + h, fill=col, outline=outline,
                           width=width)
        self._rect_nodes.append((n, x, y, w, h))
        if marked and w > 30 and h > 20:
            c.create_text(x + w - 6, y + 6, text="🗑", anchor="ne",
                          font=("Microsoft YaHei UI", 9))
        if w > 42 and h > 16:
            label = tag if len(tag) <= 16 else tag[:14] + "…"
            c.create_text(x + 4, y + 3, text=label, anchor="nw", fill=tc,
                          font=("Microsoft YaHei UI", 9 if w > 80 else 8))
            if h > 34 and n.size:
                c.create_text(x + 4, y + 17, anchor="nw", text=human(n.size),
                              fill=tc if leaf != "busy" else "#BDBDBD",
                              font=("Microsoft YaHei UI", 8))

    # ---------- 面包屑 / 同级列表 ----------
    def _refresh_crumbs(self):
        for w in self.crumb_bar.winfo_children():
            w.destroy()
        n = self._focus
        comps = []
        while n is not None:
            comps.append(n)
            n = n.parent
        comps.reverse()
        for i, nd in enumerate(comps):
            lbl = nd.name if nd.name else nd.path
            b = ttk.Button(self.crumb_bar, text=lbl[:24],
                           command=lambda x=nd: self._focus_node(x))
            b.pack(side="left")
            if i < len(comps) - 1:
                ttk.Label(self.crumb_bar, text="›",
                          style="Hint.TLabel").pack(side="left")

    def _refresh_siblings(self):
        self.sib_box.delete(0, "end")
        self._sib_nodes = []
        f = self._focus
        parent = f.parent
        if parent is None:
            return
        sibs = [c for c in parent.children if c.is_dir and c is not f]
        sibs.sort(key=lambda c: -c.size)
        for c in sibs[:12]:
            self.sib_box.insert("end", f"{c.name}  —  {human(c.size)}")
            self._sib_nodes.append(c)

    def _jump_sibling(self, _evt=None):
        sel = self.sib_box.curselection()
        if sel and self._sib_nodes:
            self._focus_node(self._sib_nodes[sel[0]])

    # ---------- 交互 ----------
    def _hit_rect(self, x, y):
        for n, rx, ry, rw, rh in reversed(self._rect_nodes):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return n, (rx, ry, rw, rh)
        return None, None

    def _hit(self, x, y):
        return self._hit_rect(x, y)[0]

    def _on_click(self, ev):
        n = self._hit(ev.x, ev.y)
        self._pick = n
        self._render()
        self._show_panel(n)
        if n and n.is_dir and not n.agg and n.scanned:
            # disktree 风格:单击目录即下钻
            self._focus_node(n)

    def _on_double(self, ev):
        n = self._hit(ev.x, ev.y)
        if n is None:
            return
        if n.is_dir and n.scanned:
            self._focus_node(n)
        elif n.path:
            os.startfile(os.path.dirname(n.path))  # noqa

    def _on_motion(self, ev):
        if self._motion_job:
            self.after_cancel(self._motion_job)
        self._motion_job = self.after(150, self._do_hover, ev.x, ev.y)

    def _do_hover(self, x, y):
        self._motion_job = None
        n, rect = self._hit_rect(x, y)
        if n is self._hover:
            return
        self._hover = n
        self.canvas.delete("hover")
        if n is None:
            return
        rx, ry, rw, rh = rect
        c = self.canvas
        c.create_rectangle(rx + 1, ry + 1, rx + rw - 1, ry + rh - 1,
                           outline="#D50000", width=2, tags="hover")
        if not self._focus:
            return
        pct = (n.size / self._focus.size * 100) if self._focus.size else 0.0
        tip = f"{n.name}\n{n.path or '(聚合块)'}\n{human(n.size)} · {pct:.1f}%"
        if n.is_dir and n.file_count:
            tip += f"\n{n.file_count} 个文件"
        lines = tip.split("\n")
        tw = max(len(ln) for ln in lines) * 8 + 16
        th = len(lines) * 17 + 10
        tx = min(x + 14, c.winfo_width() - tw - 4)
        ty = min(y + 14, c.winfo_height() - th - 4)
        c.create_rectangle(tx, ty, tx + tw, ty + th, fill="#FFFDE7",
                           outline="#FBC02D", tags="hover")
        for i, ln in enumerate(lines):
            c.create_text(tx + 8, ty + 8 + i * 17, text=ln, anchor="nw",
                          fill="#333333", font=("Microsoft YaHei UI", 9),
                          tags="hover")

    def _on_leave(self, _ev):
        self._hover = None
        self.canvas.delete("hover")

    def _on_wheel(self, ev):
        step = 4
        delta = -step if ev.delta > 0 else step
        self._zoom = max(self.ZOOM_RANGE[0],
                         min(self.ZOOM_RANGE[1], self._zoom + delta))
        self._render()

    def _focus_node(self, n):
        if n is None or not n.is_dir:
            return
        if self._focus is not None and n is not self._focus:
            self._stack.append(self._focus)
        self._focus = n
        self._pick = None
        self.btn_back.config(state="normal" if self._stack else "disabled")
        self._show_panel(None)
        self._refresh_siblings()
        self._render()

    def _on_right(self, ev):
        n = self._hit(ev.x, ev.y)
        if n is not None:
            self._pick = n
            self._render()
            self._show_panel(n)
            self.menu.tk_popup(ev.x_root, ev.y_root)

    def _back(self):
        if self._stack:
            self._focus = self._stack.pop()
            self.btn_back.config(state="normal" if self._stack
                                 else "disabled")
            self._refresh_siblings()
            self._render()

    def _home(self):
        self._stack = []
        self._focus = self._show_root
        self.btn_back.config(state="disabled")
        self._refresh_siblings()
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

    # ---------- 选中面板(含应用归属识别) ----------
    def _show_panel(self, n):
        if n is None:
            self.lbl_name.config(text="—")
            self.lbl_size.config(text="")
            self.lbl_pct.config(text="")
            self.lbl_meta.config(text="")
            self.lbl_owner.config(text="")
            self.btn_mark.config(state="disabled")
            return
        self.lbl_name.config(text=n.name)
        self.lbl_size.config(text=human(n.size))
        pct = (n.size / self._focus.size * 100) if (self._focus and
                                                    self._focus.size) else 0
        self.lbl_pct.config(text=f"占当前视图 {pct:.1f}%"
                            + (f" · 占扫描总量 "
                               f"{n.size / self._show_root.size * 100:.1f}%"
                               if self._show_root and self._show_root.size
                               else ""))
        metas = []
        if n.is_dir:
            metas.append(f"文件数: {n.file_count}")
        if n.mtime:
            import datetime
            metas.append("最后修改: " + datetime.datetime.fromtimestamp(
                n.mtime).strftime("%Y-%m-%d %H:%M"))
        if n.agg:
            metas.append("聚合块(多个小文件,聚焦目录后可细分)")
        self.lbl_meta.config(text="\n".join(metas))
        if n is self._show_root or n.agg or not n.path:
            self.lbl_owner.config(text="")
            self.btn_mark.config(state="disabled")
            return
        self.btn_mark.config(state="normal")
        self._owner_seq += 1
        seq = self._owner_seq
        self.lbl_owner.config(text="归属: 识别中…")
        threading.Thread(target=self._identify_worker,
                         args=(n.path, seq), daemon=True).start()

    def _identify_worker(self, path, seq):
        disp, src = None, None
        try:
            import app_identity
            disp, src = app_identity.identify(path)
        except Exception:
            disp, src = None, None
        self.after(0, lambda: self._show_owner(seq, disp, src))

    def _show_owner(self, seq, disp, src):
        if seq != self._owner_seq:
            return
        if disp:
            self.lbl_owner.config(text=f"属于: {disp} ｜ 来源: {src}")
        else:
            self.lbl_owner.config(text="归属: 未识别")

    # ---------- 标记待删 ----------
    def _toggle_mark(self, _evt=None):
        n = self._pick
        if not n or n is self._show_root or n.agg or not n.path:
            if n and n.agg:
                messagebox.showinfo("提示", "聚合块包含多个小文件,"
                                            "请聚焦到目录后标记具体文件",
                                    parent=self)
            return
        if n in self._marked:
            self._marked.remove(n)
        elif n.is_dir:
            self._marked = absorb_marked(self._marked, n)
        else:
            self._marked.append(n)
        self._refresh_mark_list()
        self._render()

    def _refresh_mark_list(self):
        self.mark_box.delete(0, "end")
        for n in self._marked:
            self.mark_box.insert("end", f"{n.name}  {human(n.size)}")
        total = sum(n.size for n in self._marked)
        self.lbl_release.config(
            text=f"合计 {len(self._marked)} 项,预计释放 {human(total)}")

    def _review_delete(self):
        marked = [n for n in self._marked if n.path]
        if not marked:
            messagebox.showinfo("提示", "先用空格标记要删除的项目",
                                parent=self)
            return
        try:
            import orphan_scanner as osc
        except ImportError:
            osc = None
        protected, deletable = [], []
        for n in marked:
            if osc and osc.is_protected_path(n.path):
                protected.append(n)
            else:
                deletable.append(n)
        total = sum(n.size for n in deletable)
        lines = [f"  {n.name}  {human(n.size)}\n      {n.path}"
                 for n in deletable[:25]]
        if len(deletable) > 25:
            lines.append(f"  … 等共 {len(deletable)} 项")
        msg = (f"将把 {len(deletable)} 项移入应用回收站(可还原),\n"
               f"预计释放约 {human(total)}\n\n" + "\n".join(lines))
        if protected:
            msg += (f"\n\n⛔ {len(protected)} 项为系统受保护目录,"
                    f"已自动跳过:\n"
                    + "\n".join(f"  {n.path}" for n in protected[:5]))
        if not deletable:
            messagebox.showwarning("无法删除", "所选项目全部位于系统受保护"
                                              "目录,已拒绝", parent=self)
            return
        if not messagebox.askyesno("复核删除(预览)", msg, icon="warning",
                                   parent=self):
            return
        self.lbl_stat.config(text="正在移入回收站…")
        threading.Thread(target=self._trash_marked_worker,
                         args=(deletable,), daemon=True).start()

    def _trash_marked_worker(self, items):
        ok_paths, fails = set(), []
        for n in items:
            try:
                if self.on_trash:
                    ok, m = self.on_trash(n.path)
                else:
                    import orphan_scanner as o
                    ok, m, _meta = o.move_to_trash(n.path)
                if ok:
                    ok_paths.add(n.path)
                    p = n.parent
                    while p is not None:
                        p.size = max(0, p.size - n.size)
                        p = p.parent
                else:
                    fails.append((n.path, m))
            except OSError as e:
                fails.append((n.path, str(e)))
        self.after(0, lambda: self._after_trash_marked(ok_paths, fails, items))

    def _after_trash_marked(self, ok_paths, fails, items):
        for n in items:
            if n in self._marked:
                self._marked.remove(n)
            if n.path in ok_paths and n.parent is not None \
                    and n in n.parent.children:
                n.parent.children.remove(n)
        self._refresh_mark_list()
        self._pick = None
        self._show_panel(None)
        self._render()
        if fails:
            self.lbl_stat.config(
                text=f"完成:{len(ok_paths)} 成功 / {len(fails)} 失败")
            messagebox.showwarning(
                "部分失败",
                "\n".join(f"{p}: {m}" for p, m in fails[:6]), parent=self)
        else:
            self.lbl_stat.config(
                text=f"已移入回收站 {len(ok_paths)} 项 ✅"
                     f"(可从主窗口回收站还原)")

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
        try:
            import orphan_scanner as osc
            if osc.is_protected_path(n.path):
                messagebox.showerror("拒绝删除",
                                     f"系统受保护目录,拒绝删除:\n{n.path}",
                                     parent=self)
                return
        except Exception:
            pass
        if not messagebox.askyesno(
                "移至回收站",
                f"把以下项目移到回收站(可还原)?\n\n{n.path}"
                f"\n大小: {human(n.size)}",
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
