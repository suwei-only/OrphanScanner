# -*- coding: utf-8 -*-
"""应用归属识别内核:任意文件/目录路径 → 属于哪个软件
=====================================================
数据源(按优先级):
  1. 卸载注册表 InstallLocation + DisplayName(复用 orphan_scanner.uninstall_entries)
  2. Steam / Epic manifest(acf 的 name+installdir;.item 的 DisplayName+InstallLocation)
  3. 内置知识库:中国常见应用数据/安装路径组件匹配
  4. 开始菜单 .lnk(复用 orphan_scanner.start_menu_links)
  5. UWP Get-AppxPackage InstallLocation
  6. 运行中的进程所在目录
匹配规则:路径大小写不敏感(normcase);目录前缀最深匹配;同深度按来源优先级。
知识库为路径组件匹配(任一目录名等于键),只胜过 lnk/UWP/进程来源。

纯函数可测:identify(path, sources=None) 可注入 sources 列表。
"""
import os
import re
import glob
import time
import json

# 内置知识库:(显示名, 路径组件键,小写)
APP_KB = [
    ("微信(腾讯)", "wechat files"),
    ("微信(腾讯)", "xwechat_files"),
    ("微信(腾讯)", "wechatwin"),
    ("QQ(腾讯)", "tencent files"),
    ("企业微信(腾讯)", "wxwork"),
    ("钉钉(阿里)", "dingtalk"),
    ("百度网盘(百度)", "baidunetdisk"),
    ("百度网盘(百度)", "baidunetdiskdownload"),
    ("百度网盘(百度)", "baiduyundownload"),
    ("夸克(阿里巴巴)", "quarkclouddrive"),
    ("夸克(阿里巴巴)", "quark"),
    ("网易云音乐(网易)", "cloudmusic"),
    ("Google Chrome(谷歌)", "chrome"),
    ("Microsoft Edge(微软)", "edge"),
    ("JetBrains IDE", "jetbrains"),
    ("VS Code(微软)", "microsoft vs code"),
    ("npm 依赖缓存", "node_modules"),
    ("pip 缓存", "pip"),
    ("conda 环境", "anaconda"),
    ("conda 环境", "miniconda"),
    ("Steam 游戏库", "steamapps"),
    ("Steam 游戏库", "steamlibrary"),
    ("Epic Games", "epic games"),
    ("微信开发者工具(腾讯)", "wechat devtools"),
]

# 来源优先级:数字小 = 优先
SOURCE_PRIORITY = {
    "注册表卸载项": 1,
    "Steam 游戏库": 2,
    "Epic 游戏库": 2,
    "内置知识库": 3,
    "开始菜单快捷方式": 4,
    "UWP 应用": 5,
    "运行中的程序": 6,
}

# sources 缓存(TTL 秒;PS 调用较慢,选中文件时避免重复收集)
_CACHE = {"ts": 0.0, "sources": []}
CACHE_TTL = 60.0


def invalidate_cache():
    """清空缓存(测试/重新扫描后调用)"""
    _CACHE["ts"] = 0.0
    _CACHE["sources"] = []


def get_sources(force=False):
    """收集全部归属来源: [(normcase_path, display_name, source_label)]"""
    now = time.time()
    if not force and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["sources"]
    _CACHE["sources"] = _collect_sources()
    _CACHE["ts"] = now
    return _CACHE["sources"]


def _collect_sources():
    out = []
    try:
        import orphan_scanner as o
    except ImportError:
        o = None
    # ① 卸载注册表
    if o is not None:
        try:
            for _root, _keypath, d in o.uninstall_entries():
                loc = (d.get("InstallLocation") or "").strip().strip('"')
                dn = (d.get("DisplayName") or "").strip()
                if loc and dn:
                    out.append((os.path.normcase(os.path.normpath(loc)),
                                dn, "注册表卸载项"))
        except Exception:
            pass
    # ② Steam / Epic
    out += _steam_sources()
    out += _epic_sources()
    # ④ 开始菜单 .lnk
    if o is not None:
        try:
            for name, exe in o.start_menu_links().items():
                out.append((os.path.normcase(os.path.dirname(exe)),
                            name, "开始菜单快捷方式"))
        except Exception:
            pass
    # ⑤ UWP
    out += _uwp_sources()
    # ⑥ 运行进程
    if o is not None:
        try:
            for d in o.running_process_dirs():
                out.append((os.path.normcase(d), "正在运行的程序",
                            "运行中的程序"))
        except Exception:
            pass
    return _dedupe(out)


def _dedupe(rows):
    """同 (root, source) 去重,保留 display 最长者;丢弃根太短/无意义的条目"""
    best = {}
    order = []
    for root, disp, src in rows:
        if not root or len(root) < 3:
            continue
        key = (root, src)
        if key not in best or len(disp) > len(best[key][0]):
            best[key] = (disp, src)
        if key not in order:
            order.append(key)
    return [(k[0], best[k][0], best[k][1]) for k in order]


def _steam_sources():
    rows = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as k:
            steam, _ = winreg.QueryValueEx(k, "SteamPath")
    except OSError:
        return rows
    libs = [steam]
    seen = {os.path.normcase(steam)}
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf):
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for m in re.finditer(r'^\s*"path"\s+"([^"]+)"', f.read(),
                                     re.M):
                    p = m.group(1).replace("\\\\", "\\")
                    if os.path.normcase(p) not in seen and os.path.isdir(p):
                        seen.add(os.path.normcase(p))
                        libs.append(p)
        except OSError:
            pass
    for lib in libs:
        sa = os.path.join(lib, "steamapps")
        for acf in glob.glob(os.path.join(sa, "appmanifest_*.acf")):
            try:
                with open(acf, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
                m1 = re.search(r'"installdir"\s+"([^"]*)"', txt)
                m2 = re.search(r'"name"\s+"([^"]*)"', txt)
                if m1 and m2 and m1.group(1):
                    rows.append((
                        os.path.normcase(os.path.join(
                            sa, "common", m1.group(1))),
                        m2.group(1), "Steam 游戏库"))
            except OSError:
                pass
    return rows


def _epic_sources():
    rows = []
    candidates = [r"C:\Program Files\Epic Games",
                  r"C:\Program Files (x86)\Epic Games",
                  r"E:\epic", r"E:\Epic Games",
                  r"D:\Epic Games", r"F:\Epic Games"]
    for c in candidates:
        mdir = os.path.join(c, "Epic Games Launcher", "Data", "Manifests")
        if not os.path.isdir(mdir):
            continue
        for mf in glob.glob(os.path.join(mdir, "*.item")):
            try:
                with open(mf, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                loc = (data.get("InstallLocation") or "").strip()
                dn = (data.get("DisplayName") or "").strip()
                if loc and dn:
                    rows.append((os.path.normcase(os.path.normpath(loc)),
                                 dn, "Epic 游戏库"))
            except Exception:
                pass
    return rows


def _uwp_sources():
    rows = []
    try:
        import subprocess
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-AppxPackage | Where-Object {$_.InstallLocation} | "
              "ForEach-Object { \"$($_.Name)|$($_.InstallLocation)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, loc = line.split("|", 1)
            loc = loc.strip()
            if name and len(loc) > 2 and loc[1:2] == ":":
                rows.append((os.path.normcase(os.path.normpath(loc)),
                             name, "UWP 应用"))
    except Exception:
        pass
    return rows


def _has_component(norm_path, key):
    """路径任一目录名(小写)等于 key"""
    for part in norm_path.split(os.sep):
        if part == key:
            return True
    return False


def identify(path, sources=None):
    """识别路径归属 → (display_name, source_label);未知 → (None, None)"""
    if not path:
        return None, None
    p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    if sources is None:
        sources = get_sources()
    # 路径型来源:目录前缀最深匹配,同深按优先级
    best_root, best_disp, best_src = None, None, None
    best_len, best_pri = -1, 99
    for root, disp, src in sources:
        if p == root or p.startswith(root + os.sep):
            pri = SOURCE_PRIORITY.get(src, 99)
            if (len(root) > best_len
                    or (len(root) == best_len and pri < best_pri)):
                best_root, best_disp, best_src = root, disp, src
                best_len, best_pri = len(root), pri
    # 知识库:路径组件匹配,取最长键;只胜过 lnk/UWP/进程(优先级 > 3)或空结果
    kb_disp, kb_len = None, -1
    for disp, key in APP_KB:
        if _has_component(p, key) and len(key) > kb_len:
            kb_disp, kb_len = disp, len(key)
    if kb_disp and best_pri > SOURCE_PRIORITY["内置知识库"]:
        return kb_disp, "内置知识库"
    if best_disp:
        return best_disp, best_src
    return None, None
