#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OrphanScanner v2 —— 全软件孤儿/残留扫描器(三层架构)
====================================================
层1  游戏平台孤儿:  Steam(acf 清单)/ Epic(.item 清单) 已登记的目录之外,
                    common/ 里多出来的 = 卸载残留(100% 确定)
层2  注册表残留:    卸载注册表登记了 InstallLocation,但目录已不存在
                    = 卸载残留的注册表项 / 半卸载软件(高置信)
层3  启发式候选:    磁盘上无任何登记的大目录(带 exe 特征),列证据供人工判断
                    (只报告,绝不自动删)

用法:
  python orphan_scanner.py               # 全量扫描(层1+2+3)
  python orphan_scanner.py --layers 1 2  # 只跑指定层
  python orphan_scanner.py --delete      # 交互删除(层1/2 的确认项)
"""
import os, re, sys, glob, shutil, json, winreg
from datetime import datetime

# ---------------- 基础工具 ----------------
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"

def dir_size(path: str, limit: int = 3_000_000_000) -> int:
    """统计目录大小(超过 limit 字节就提前停,避免扫到巨型目录卡死)"""
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
                if total > limit:
                    return total
            except OSError:
                pass
    return total

def mtime_str(p: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")
    except OSError:
        return "?"

def find_exe(p: str, depth: int = 2) -> str:
    """找目录里的主程序(浅层),作为'像软件'的证据"""
    base = p.rstrip("\\/").count(os.sep)
    for cur, dirs, files in os.walk(p):
        if cur.count(os.sep) - base > depth:
            dirs[:] = []
            continue
        for f in files:
            if f.lower().endswith(".exe"):
                return os.path.join(cur, f)
    return ""

def find_save_dirs(p: str, depth: int = 3) -> list:
    hits = []
    base = p.rstrip("\\/").count(os.sep)
    for cur, dirs, _ in os.walk(p):
        if cur.count(os.sep) - base > depth:
            dirs[:] = []
            continue
        for d in dirs:
            low = d.lower()
            if any(k in low for k in ("saved", "savegame", "backup",
                                      "storages", "localstate")):
                hits.append(os.path.join(cur, d))
    return hits

# ---------------- 层1:游戏平台 ----------------
def steam_orphans():
    """Steam 各库 common 下无 appmanifest 对应的目录"""
    orphans = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as k:
            steam, _ = winreg.QueryValueEx(k, "SteamPath")
    except OSError:
        return orphans
    libs = [steam]
    seen = {os.path.normcase(steam)}
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf):
        with open(vdf, encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'^\s*"path"\s+"([^"]+)"', f.read(), re.M):
                p = m.group(1).replace("\\\\", "\\")
                if os.path.normcase(p) not in seen and os.path.isdir(p):
                    seen.add(os.path.normcase(p))
                    libs.append(p)
    whitelist = {"steamworks shared", "steam controller configs"}
    for lib in libs:
        sa = os.path.join(lib, "steamapps")
        common = os.path.join(sa, "common")
        if not os.path.isdir(common):
            continue
        installed = {parse_installdir(a).lower() for a in
                     glob.glob(os.path.join(sa, "appmanifest_*.acf"))}
        for e in os.scandir(common):
            if e.is_dir() and e.name.lower() not in installed \
                    and e.name.lower() not in whitelist:
                orphans.append((e.path, "Steam 卸载残留",
                                mtime_str(e.path)))
    return orphans

def parse_installdir(acf: str) -> str:
    try:
        with open(acf, encoding="utf-8", errors="replace") as f:
            m = re.search(r'"installdir"\s+"([^"]*)"', f.read())
            return m.group(1) if m else ""
    except OSError:
        return ""

def epic_orphans():
    """Epic: 比较 .item 清单登记的 InstallLocation 与实际目录"""
    orphans = []
    # 已知 Epic 库位置(常见)
    candidates = [r"C:\Program Files\Epic Games",
                  r"C:\Program Files (x86)\Epic Games",
                  r"E:\epic", r"E:\Epic Games", r"D:\Epic Games",
                  r"F:\Epic Games"]
    manifests = []
    for c in candidates:
        mdir = os.path.join(c, "Epic Games Launcher", "Data", "Manifests")
        if os.path.isdir(mdir):
            manifests += glob.glob(os.path.join(mdir, "*.item"))
        # 库目录里可能直接有游戏 + .egstore
        if os.path.isdir(c):
            for e in os.scandir(c):
                if e.is_dir() and os.path.isdir(os.path.join(
                        e.path, ".egstore")):
                    manifests.append(("DIR", e.path))
    if not manifests:
        return orphans
    registered = set()
    for mf in manifests:
        if isinstance(mf, tuple):
            registered.add(mf[1].lower())
            continue
        try:
            with open(mf, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            loc = data.get("InstallLocation", "")
            if loc:
                registered.add(loc.lower())
        except Exception:
            pass
    for c in candidates:
        if not os.path.isdir(c):
            continue
        for e in os.scandir(c):
            if e.is_dir() and e.name.lower() not in ("epic games launcher",):
                # 目录带 .egstore 才算游戏;无清单登记且像游戏的才算孤儿
                if e.path.lower() not in registered and os.path.isdir(
                        os.path.join(e.path, ".egstore")):
                    orphans.append((e.path, "Epic 卸载残留", mtime_str(e.path)))
    return orphans

# ---------------- 层2:注册表残留 ----------------
def uninstall_entries():
    """返回 (root, 卸载键路径, 值dict) 列表"""
    rows = []
    bases = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, path in bases:
        try:
            k = winreg.OpenKey(root, path)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                    i += 1
                except OSError:
                    break
                try:
                    sk = winreg.OpenKey(root, path + "\\" + sub)
                    d = {}
                    for key in ("DisplayName", "InstallLocation",
                                "UninstallString", "DisplayIcon"):
                        try:
                            d[key], _ = winreg.QueryValueEx(sk, key)
                        except OSError:
                            d[key] = ""
                    rows.append((root, path + "\\" + sub, d))
                except OSError:
                    pass
        except OSError:
            pass
    return rows

def running_process_dirs() -> set:
    """收集正在运行的进程 exe 所在目录(用于排除'活着的软件')"""
    dirs = set()
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-Process | Where-Object {$_.Path} | "
             "Select-Object -ExpandProperty Path -Unique"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and line[1:2] == ":" and os.path.isfile(line):
                dirs.add(os.path.normcase(os.path.dirname(line)))
    except Exception:
        pass
    return dirs

def start_menu_links() -> dict:
    """开始菜单快捷方式: {lnk名(小写,无.lnk): exe路径(仅当exe存在)}
    软件迁移目录后,开始菜单快捷方式通常指向新位置且仍有效"""
    out = {}
    try:
        import subprocess
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "$sh = New-Object -ComObject WScript.Shell; "
              "Get-ChildItem -Path \"$env:ProgramData\\Microsoft\\Windows\\"
              "Start Menu\", \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\" "
              "-Recurse -Filter *.lnk -ErrorAction SilentlyContinue | "
              "ForEach-Object { $sc = $sh.CreateShortcut($_.FullName); "
              "\"$($_.BaseName)|$($sc.TargetPath)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=90,
                           encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, target = line.split("|", 1)
            target = target.strip()
            if name and target[1:2] == ":" and os.path.isfile(target):
                out[name.lower()] = os.path.normcase(target)
    except Exception:
        pass
    return out

def registry_leftovers():
    """注册表登记了 InstallLocation 但目录已消失(高置信残留)
    四重校验:InstallLocation 失效 + 卸载串指向的文件不存在
             + 不在运行进程 + 开始菜单无同名的存活快捷方式
    返回: (注册表键路径, 显示名, 说明) —— 清理动作是删注册表键,不是删目录"""
    proc = running_process_dirs()
    links = start_menu_links()
    out = []
    for root, keypath, d in uninstall_entries():
        loc = (d.get("InstallLocation") or "").strip().strip('"')
        if not loc or os.path.isdir(loc):
            continue
        alive = False
        us = (d.get("UninstallString") or "").strip()
        # 卸载串里的本地 exe/msi 路径
        for tok in us.replace('"', " ").split():
            if re.match(r"^[A-Za-z]:", tok) and tok.lower().endswith(
                    (".exe", ".msi")) and os.path.isfile(tok):
                alive = True
                break
        if os.path.normcase(loc) in proc:
            alive = True
        # 软件可能迁移过目录:开始菜单有同名快捷方式且目标 exe 存活
        if not alive:
            dn = (d.get("DisplayName") or "").strip().lower()
            if dn:
                first = dn.split()[0]
                if len(first) >= 3:
                    for lnk_name, exe in links.items():
                        if lnk_name == dn or first in lnk_name \
                                or lnk_name.startswith(first):
                            alive = True
                            break
        if not alive:
            rootname = ("HKEY_LOCAL_MACHINE" if root == winreg.HKEY_LOCAL_MACHINE
                        else "HKEY_CURRENT_USER")
            out.append((f"{rootname}\\{keypath}",
                        d.get("DisplayName") or "(未命名软件)",
                        f"僵尸注册表项,指向已不存在的 {loc}"))
    return out

# ---------------- 层3:启发式候选 ----------------
# 已知"正常但无 InstallLocation 登记"的系统组件/开发工具,直接排除
SYSTEM_COMPONENTS = {
    "dotnet", "microsoft sql server", "nvidia gpu computing toolkit",
    "windows kits", "reference assemblies", "msbuild", "microsoft sdks",
    "microsoft edge", "microsoft", "wsl", "lghub", "epic online services",
    "common files", "internet explorer", "windows defender",
    "windows nt", "windows photo viewer", "windows portable devices",
    "windows side by side", "windowsapps", "windows mail", "windows media player",
    "windows multimedia platform", "microsoft visual studio", "java",
    "common files", "uninstall information", "windowspowerShell",
    "windows security", "vmware", "docker", "git", "anaconda", "python",
    "matlab runtime", "nvidia corporation", "intel", "amd", "nvidia",
    "windows kits", "microsoft.net", "nuget packages", "xamarin",
    "microsoft shared", "windows defender advanced threat protection",
    "internet explorer", "windows ai", "windows update",
    "common files", "msbuild", "microsoft sdks", "windows kits",
    "reference assemblies", "microsoft visual studio 14.0",
    "microsoft visual studio 15.0", "microsoft visual studio 16.0",
    "microsoft visual studio 17.0", "microsooft visual studio",
    "windows kts", "microsoft.net framework", "windows sidebar",
    "common files", "windows media", "windows nt", "windows photo viewer",
}

def heuristic_candidates(roots, min_size=200_000_000):
    """扫描各盘常见安装根目录:无登记的大目录 -> 候选
    排除:系统组件白名单 / 注册表已登记 / 正在运行的进程目录"""
    registered = set()
    for root, keypath, d in uninstall_entries():
        loc = (d.get("InstallLocation") or "").strip().strip('"')
        if loc:
            registered.add(os.path.normcase(os.path.normpath(loc)))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as k:
            steam, _ = winreg.QueryValueEx(k, "SteamPath")
        registered.add(os.path.normcase(os.path.normpath(steam)))
    except OSError:
        pass
    proc_dirs = running_process_dirs()

    skip_names = {"windows", "program files", "program files (x86)",
                  "programdata", "users", "$recycle.bin", "system volume "
                  "information", "recovery", "intel", "amd", "nvidia",
                  "msys64", "anaconda", "python", "node_modules",
                  "appdata", "epic games launcher", "common files",
                  "microsoft", "windows kits", "dotnet", "msbuild",
                  "reference assemblies", "microsoft sdks", "wsl"}
    cands = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for e in os.scandir(root):
            if not e.is_dir():
                continue
            low = e.name.lower()
            if low in skip_names or low in SYSTEM_COMPONENTS:
                continue
            key = os.path.normcase(os.path.normpath(e.path))
            if key in registered:
                continue
            # 正在运行的进程所在目录 = 活软件,排除
            if key in proc_dirs or any(
                    key.startswith(p) for p in proc_dirs):
                continue
            try:
                st = e.stat()
                age_days = (datetime.now() -
                            datetime.fromtimestamp(st.st_mtime)).days
            except OSError:
                continue
            if age_days < 90:   # 最近动过,多半在用
                continue
            sz = dir_size(e.path)
            if sz >= min_size:
                exe = find_exe(e.path)
                saves = find_save_dirs(e.path)
                cands.append({
                    "path": e.path, "size": sz, "mtime": mtime_str(e.path),
                    "age_days": age_days, "exe": exe, "saves": saves,
                })
    return cands

# ---------------- GUI / 结构化 API ----------------
APP_VERSION = "1.1.0"
TRASH_DIR_NAME = ".OrphanTrash"   # 应用回收站(可还原,不直接物理删除)


def drive_of(path: str) -> str:
    """返回路径所在盘根,如 D:\\"""
    p = os.path.abspath(path)
    return os.path.splitdrive(p)[0] + "\\"


def trash_root(path: str) -> str:
    """目标所在盘的回收站目录"""
    return os.path.join(drive_of(path), TRASH_DIR_NAME)


def _meta_file(trash: str) -> str:
    return os.path.join(trash, ".meta.json")


def _load_meta(trash: str) -> list:
    try:
        with open(_meta_file(trash), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_meta(trash: str, items: list):
    os.makedirs(trash, exist_ok=True)
    with open(_meta_file(trash), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def trash_items() -> list:
    """返回全部回收站条目:
    [{trash, name, orig, moved_to, ts, kind(dir/file/reg), reg_key, reg_file}]"""
    out = []
    for letter in "CDEF":
        drv = letter + ":\\"
        tdir = os.path.join(drv, TRASH_DIR_NAME)
        if os.path.isdir(tdir):
            for it in _load_meta(tdir):
                it["trash"] = tdir
                out.append(it)
    return out


def move_to_trash(path: str) -> tuple:
    """把文件/目录移入回收站(同盘 rename,瞬时完成,可还原)
    返回 (ok, msg, meta条目或None)"""
    if not os.path.exists(path):
        return False, f"路径不存在: {path}", None
    trash = trash_root(path)
    name = os.path.basename(path.rstrip("\\/")) or "item"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(trash, f"{name}_{ts}")
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(trash, f"{name}_{ts}_{i}")
        i += 1
    try:
        os.makedirs(trash, exist_ok=True)
        shutil.move(path, dest)   # 同盘移动,瞬时
        meta = {"name": name, "orig": os.path.normpath(path),
                "moved_to": dest, "ts": ts,
                "kind": "dir" if os.path.isdir(dest) else "file"}
        items = _load_meta(trash)
        items.append(meta)
        _save_meta(trash, items)
        return True, f"已移入回收站(可还原): {dest}", meta
    except OSError as e:
        return False, f"移入回收站失败: {e}", None


def backup_reg_key(keypath: str) -> tuple:
    """删除注册表键前先导出 .reg 备份到回收站
    返回 (ok, msg, meta条目)"""
    try:
        parts = keypath.split("\\", 1)
        rootname, subpath = parts[0], parts[1]
    except Exception:
        return False, "无法解析键路径", None
    trash = os.path.join(drive_of("C:\\"), TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = subpath.rstrip("\\").split("\\")[-1][:40] or "registry"
    regfile = os.path.join(trash, f"reg_{name}_{ts}.reg")
    try:
        import subprocess
        r = subprocess.run(["reg", "export", keypath, regfile, "/y"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        if not os.path.isfile(regfile) or os.path.getsize(regfile) < 10:
            return False, "reg export 失败(键可能无权限或已不存在)", None
        meta = {"name": f"注册表: {name}", "orig": keypath,
                "moved_to": regfile, "ts": ts, "kind": "reg",
                "reg_key": keypath, "reg_file": regfile}
        items = _load_meta(trash)
        items.append(meta)
        _save_meta(trash, items)
        return True, f"注册表键已备份到 {regfile}", meta
    except Exception as e:
        return False, f"备份失败: {e}", None


def _item_trash(meta: dict) -> str:
    """条目所在回收站目录(meta 无 trash 键时按 moved_to 所在盘推导)"""
    t = meta.get("trash")
    if t:
        return t
    mv = meta.get("moved_to") or meta.get("reg_file") or "C:\\"
    return trash_root(mv)


def restore_item(meta: dict) -> tuple:
    """从回收站还原条目。返回 (ok, msg)"""
    trash = _item_trash(meta)
    kind = meta.get("kind")
    try:
        if kind == "reg":
            rf = meta.get("reg_file")
            if not rf or not os.path.isfile(rf):
                return False, f"备份文件丢失: {rf}"
            # 还原注册表 = 导入 .reg;HKCU 直接导入,HKLM 走 UAC 提权
            try:
                import subprocess
                key = (meta.get("reg_key") or "").upper()
                if key.startswith("HKEY_CURRENT_USER"):
                    r = subprocess.run(["reg", "import", rf],
                                       capture_output=True, text=True,
                                       timeout=60, encoding="utf-8",
                                       errors="replace")
                else:
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f'Start-Process reg -Verb RunAs -Wait -ArgumentList '
                         f'\'import "{rf}"\''],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace")
            except Exception as e:
                return False, f"导入失败: {e}"
            # 成功则从回收站移除记录并删备份
            items = [i for i in _load_meta(trash)
                     if i.get("moved_to") != meta.get("moved_to")]
            _save_meta(trash, items)
            try:
                os.remove(rf)
            except OSError:
                pass
            return True, "注册表键已还原(通过 .reg 导入)"
        # 文件/目录:移回原位置
        src = meta.get("moved_to")
        orig = meta.get("orig")
        if not src or not os.path.exists(src):
            return False, f"回收站条目丢失: {src}"
        if not orig:
            return False, "缺少原始路径信息"
        if os.path.exists(orig):
            # 原位置被占:还原到原名+后缀
            d = os.path.dirname(orig)
            base = os.path.basename(orig)
            alt = os.path.join(d, f"{base}_restored")
            k = 1
            while os.path.exists(alt):
                alt = os.path.join(d, f"{base}_restored{k}")
                k += 1
            orig = alt
        os.makedirs(os.path.dirname(orig), exist_ok=True)
        shutil.move(src, orig)   # 同盘移动
        items = [i for i in _load_meta(trash)
                 if i.get("moved_to") != meta.get("moved_to")]
        _save_meta(trash, items)
        return True, f"已还原到 {orig}"
    except OSError as e:
        return False, f"还原失败: {e}"


def purge_item(meta: dict) -> tuple:
    """从回收站永久删除条目(真正释放空间,不可恢复)"""
    trash = _item_trash(meta)
    src = meta.get("moved_to")
    try:
        if meta.get("kind") == "reg":
            if src and os.path.isfile(src):
                os.remove(src)
        elif src and os.path.exists(src):
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        items = [i for i in _load_meta(trash)
                 if i.get("moved_to") != meta.get("moved_to")]
        _save_meta(trash, items)
        return True, "已永久删除"
    except OSError as e:
        return False, f"删除失败: {e}"


def empty_trash() -> tuple:
    """清空所有盘的回收站(永久,不可恢复)"""
    removed = 0
    for letter in "CDEF":
        tdir = os.path.join(letter + ":\\", TRASH_DIR_NAME)
        if os.path.isdir(tdir):
            try:
                shutil.rmtree(tdir)
                removed += 1
            except OSError:
                pass
    return True, f"已清空 {removed} 个回收站目录"


def trash_total_size() -> int:
    """回收站占用总字节(逐项统计,可能慢)"""
    total = 0
    for it in trash_items():
        p = it.get("moved_to")
        if p and os.path.exists(p):
            try:
                if os.path.isdir(p):
                    total += dir_size(p, limit=1 << 62)
                else:
                    total += os.path.getsize(p)
            except OSError:
                pass
    return total


def scan_all(layers=(1, 2, 3)):
    """一次扫描,返回结构化结果列表:
    [{cat: 'Steam孤儿'|'Epic孤儿'|'注册表残留'|'启发式候选',
      target: 路径或注册表键, note: 说明, size: 字节或None,
      saves: 存档子目录列表(启发式), risk: 0低|1中|2高}]
    启发式候选 risk=2(可能误报), 注册表残留 risk=0, 平台孤儿 risk=0"""
    import string
    results = []

    if 1 in layers:
        for p, why, mt in steam_orphans():
            results.append({"cat": "Steam孤儿", "target": p, "note": why,
                            "size": None, "saves": [], "risk": 0})
        for p, why, mt in epic_orphans():
            results.append({"cat": "Epic孤儿", "target": p, "note": why,
                            "size": None, "saves": [], "risk": 0})

    if 2 in layers:
        for keypath, name, why in registry_leftovers():
            results.append({"cat": "注册表残留", "target": keypath,
                            "note": f"{name} | {why}",
                            "size": None, "saves": [], "risk": 0})

    if 3 in layers:
        roots = [r"C:\Program Files", r"C:\Program Files (x86)"]
        for letter in "CDEF":
            drv = letter + ":\\"
            if os.path.isdir(drv):
                roots.append(drv)
        for c in heuristic_candidates(roots):
            results.append({"cat": "启发式候选", "target": c["path"],
                            "note": f"≥90天未动 | 主程序:{c['exe'] or '无'}",
                            "size": c["size"], "saves": c["saves"],
                            "risk": 2})
    return results


def delete_registry_key(keypath: str):
    """删除注册表键。返回 (ok: bool, msg: str)
    HKCU 直接删;HKLM 需要管理员,通过 UAC 提权 reg.exe 删除"""
    try:
        parts = keypath.split("\\", 1)
        rootname, subpath = parts[0], parts[1]
    except Exception:
        return False, f"无法解析注册表键路径: {keypath}"

    if rootname == "HKEY_CURRENT_USER":
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subpath)
            return True, "已删除"
        except OSError as e:
            return False, str(e)

    # HKLM 及子键:提权删除(会弹 UAC,用户点"是")
    try:
        import subprocess
        regcmd = f'reg delete "{keypath.replace(chr(92), chr(92))}" /f'
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'Start-Process reg -Verb RunAs -Wait -ArgumentList '
             f'\'delete "{subpath}" /f\''],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        # 提权窗口结果不可见,验证键是否真的没了
        try:
            if rootname == "HKEY_LOCAL_MACHINE":
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subpath).Close()
                return False, "用户取消或删除失败(键仍存在)"
        except OSError:
            return True, "已删除(通过 UAC)"
        return False, "未知错误"
    except Exception as e:
        return False, str(e)


# ---------------- 错误反馈 / 诊断 ----------------
FEEDBACK_URL = ""   # 填入 GitHub Issues 地址后,反馈对话框会显示链接


def system_diag(error_text: str = "") -> str:
    """收集诊断信息(反馈给开发者用),不含个人隐私路径以外内容"""
    import platform
    lines = [
        "===== OrphanScanner 错误反馈 =====",
        f"版本: {APP_VERSION}",
        f"系统: {platform.system()} {platform.release()} "
        f"(版本 {platform.version()})",
        f"机器: {platform.machine()}",
        f"Python: {platform.python_version()}",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if error_text:
        lines.append("----- 错误信息 -----")
        lines.append(error_text)
    lines.append("----- 使用环境 -----")
    try:
        # Steam 是否存在
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as k:
            steam, _ = winreg.QueryValueEx(k, "SteamPath")
            lines.append(f"Steam: {steam}")
    except OSError:
        lines.append("Steam: 未安装")
    disks = []
    for letter in "CDEF":
        if os.path.isdir(letter + ":\\"):
            try:
                total, used, free = shutil.disk_usage(letter + ":\\")
                disks.append(f"{letter}: 剩余 {human(free)} / 共 {human(total)}")
            except OSError:
                pass
    lines.append("磁盘: " + "; ".join(disks))
    return "\n".join(lines)


def log_error(text: str):
    """把错误追加到日志文件(与应用同目录或用户目录)"""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        logdir = base if os.access(base, os.W_OK) else os.path.expanduser("~")
        with open(os.path.join(logdir, "orphan_scanner_errors.log"),
                  "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n"
                    f"{text}\n")
    except Exception:
        pass


def register_global_excepthook():
    """把未捕获异常写入日志并弹出可见错误(打包 windowed 后 print 不可见)"""
    import tkinter as tk
    from tkinter import messagebox

    def hook(exc_type, exc_value, exc_tb):
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_error(tb)
        try:
            messagebox.showerror(
                "程序错误",
                f"发生未预期的错误:\n\n{exc_value}\n\n"
                f"详细信息已写入日志文件 orphan_scanner_errors.log。\n"
                f"可通过「❓ 反馈问题」复制诊断信息发给开发者。")
        except Exception:
            pass

    sys.excepthook = hook
    # tkinter 回调里的异常走这里
    try:
        tk.Tk.report_callback_exception = staticmethod(
            lambda self, exc, val, tb: hook(exc, val, tb))
    except Exception:
        pass


def _load_cfg():
    """加载配置:优先 exe/脚本同目录的 feedback_config.py(便于不重打包改配置),
    否则用打包内嵌的默认配置"""
    try:
        import importlib.util
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
        ext = os.path.join(base, "feedback_config.py")
        if os.path.isfile(ext):
            spec = importlib.util.spec_from_file_location(
                "feedback_config_ext", ext)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    except Exception:
        pass
    try:
        import feedback_config
        return feedback_config
    except Exception:
        return None


def send_feedback(body: str, subject: str = "") -> tuple:
    """把反馈邮件发送给开发者(QQ 邮箱 SMTP)。
    返回 (ok, msg)。配置见 feedback_config.py(已被 gitignore)"""
    cfg = _load_cfg()
    if cfg is None:
        return False, "未配置自动发送(缺少 feedback_config.py)"
    if not cfg.FEEDBACK_AUTH_CODE:
        return False, "未配置 SMTP 授权码(见 feedback_config.py 说明)"
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        subject = subject or f"OrphanScanner v{APP_VERSION} 用户反馈"
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = cfg.FEEDBACK_USER
        msg["To"] = cfg.FEEDBACK_TO
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as s:
            s.login(cfg.FEEDBACK_USER, cfg.FEEDBACK_AUTH_CODE)
            s.sendmail(cfg.FEEDBACK_USER, [cfg.FEEDBACK_TO],
                       msg.as_string())
        return True, f"已发送到 {cfg.FEEDBACK_TO}"
    except Exception as e:
        return False, f"发送失败: {e}"


# ---------------- 主流程 ----------------
def main():
    args = sys.argv[1:]
    layers = [1, 2, 3]
    if "--layers" in args:
        i = args.index("--layers")
        layers = [int(x) for x in args[i + 1].split(",")]
    want_delete = "--delete" in args

    # 各盘根(跳过 C 盘系统区,启发式默认扫 D/E/F + C 盘常见软件目录)
    import string
    roots = []
    for letter in "CDEF":
        drv = letter + ":\\"
        if os.path.isdir(drv):
            roots.append(drv)
    heur_roots = [r"C:\Program Files", r"C:\Program Files (x86)"]
    for drv in roots:
        heur_roots.append(drv)

    findings = []   # (path, 类别, 说明, 大小或None)

    if 1 in layers:
        print("【层1】游戏平台孤儿 ...")
        for p, why, mt in steam_orphans():
            findings.append((p, "Steam孤儿", why, None))
        for p, why, mt in epic_orphans():
            findings.append((p, "Epic孤儿", why, None))

    if 2 in layers:
        print("【层2】注册表残留 ...")
        for keypath, name, why in registry_leftovers():
            findings.append((keypath, "注册表残留", f"{name} | {why}", None))

    if 3 in layers:
        print("【层3】启发式候选(无登记大目录) ...")
        for c in heuristic_candidates(heur_roots):
            findings.append((c["path"], "启发式候选",
                             f"≥90天未动 | 主程序:{c['exe'] or '无'}"
                             f"{' | 含存档:' + str(len(c['saves'])) + '处' if c['saves'] else ''}",
                             c["size"]))

    print(f"\n===== 共发现 {len(findings)} 项 =====\n")
    for idx, (p, cat, why, sz) in enumerate(findings, 1):
        szs = human(sz) if sz else "—"
        flag = "⚠️" if cat == "启发式候选" else "✅"
        print(f"{flag} [{idx}] {cat}")
        print(f"    目标: {p}")
        print(f"    说明: {why}   ({szs})")
        if cat == "注册表残留":
            print("    (清理动作 = 删除此注册表键;恢复需要重装软件)")
        print()

    if want_delete:
        print("===== 删除阶段(层1删目录;层2删注册表键;启发式候选请自行核对) =====")
        for idx, (p, cat, why, sz) in enumerate(findings, 1):
            if cat == "启发式候选":
                continue
            tip = "注册表键" if cat == "注册表残留" else "目录"
            ans = input(f"删除 [{idx}] {tip} {p} ? [y/N] ").strip().lower()
            if ans != "y":
                print("  跳过")
                continue
            try:
                if cat == "注册表残留":
                    # 拆 root 与路径:形如 HKEY_LOCAL_MACHINE\...\Uninstall\xxx
                    parts = p.split("\\", 1)
                    rootmap = {"HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
                               "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER}
                    root = rootmap.get(parts[0], winreg.HKEY_LOCAL_MACHINE)
                    winreg.DeleteKey(root, parts[1])
                    print(f"  ✅ 已删除注册表键: {p}")
                elif os.path.isdir(p):
                    shutil.rmtree(p)
                    print(f"  ✅ 已删除目录: {p}")
                else:
                    os.remove(p)
                    print(f"  ✅ 已删除文件: {p}")
            except OSError as e:
                print(f"  ❌ 失败: {e}")
    else:
        print("(只读模式。加 --delete 可交互删除层1目录/层2注册表键)")

if __name__ == "__main__":
    main()
