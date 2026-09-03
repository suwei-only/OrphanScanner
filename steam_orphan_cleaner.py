#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SteamOrphanCleaner —— Steam 库孤儿文件扫描器
================================================
原理:
  1. 读注册表找到 Steam 安装目录
  2. 解析 libraryfolders.vdf 找到所有游戏库文件夹(可跨多盘)
  3. 解析每个库里的 appmanifest_*.acf(安装清单),得到
     "Steam 认为已安装"的目录名集合
  4. 扫描各库 steamapps\\common\\ 下的实际目录,
     不在清单集合里的 = 孤儿残留(卸载没删干净)
  5. 删除前检测目录内是否含 Saved/存档类子目录,给出警告

用法:
  python steam_orphan_cleaner.py             # 仅扫描,列出孤儿(安全)
  python steam_orphan_cleaner.py --delete    # 扫描后逐个交互确认删除
  python steam_orphan_cleaner.py --delete --yes   # 自动删除(慎用)
"""
import os
import re
import sys
import glob
import shutil
import winreg

# Steam 官方共享目录,虽然无 acf 但不是孤儿,不报告
WHITELIST = {
    "steamworks shared",
    "steam controller configs",
    "steamworks common redistributables",
}

def human(n: int) -> str:
    """字节数 -> 人类可读"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"

def get_steam_path() -> str:
    """从注册表读 Steam 安装目录"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as k:
            val, _ = winreg.QueryValueEx(k, "SteamPath")
            if val and os.path.isdir(val):
                return val
    except OSError:
        pass
    for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(p):
            return p
    return None

def get_library_folders(steam_path: str) -> list:
    """Steam 主目录 + libraryfolders.vdf 里登记的所有库路径"""
    libs = [steam_path]
    seen = {os.path.normcase(steam_path)}   # 统一大小写/斜杠去重
    vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf):
        with open(vdf, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in re.finditer(r'^\s*"path"\s+"([^"]+)"', text, re.M):
            p = m.group(1).replace("\\\\", "\\")
            key = os.path.normcase(p)
            if key not in seen and os.path.isdir(p):
                seen.add(key)
                libs.append(p)
    return libs

def parse_acf(path: str) -> dict:
    """解析 appmanifest_*.acf,提取 name / installdir"""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for key in ("name", "installdir"):
            m = re.search(rf'"{key}"\s+"([^"]*)"', text)
            if m:
                out[key] = m.group(1)
    except OSError:
        pass
    return out

def dir_size(path: str) -> int:
    """递归统计目录大小(容错)"""
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total

def find_save_dirs(path: str, depth: int = 3) -> list:
    """浅层查找孤儿目录里疑似存档/配置的子目录"""
    hits = []
    root_depth = path.rstrip("\\/").count(os.sep)
    for cur, dirs, _ in os.walk(path):
        if cur.count(os.sep) - root_depth > depth:
            dirs[:] = []
            continue
        for d in dirs:
            low = d.lower()
            if any(k in low for k in ("saved", "savegame", "savegame",
                                      "storages", "backup")):
                hits.append(os.path.join(cur, d))
    return hits

def scan_library(lib: str) -> dict:
    """扫描一个库,返回 {'installed': [...], 'orphans': [...], 'installed_names': set}"""
    sa = os.path.join(lib, "steamapps")
    common = os.path.join(sa, "common")
    result = {"installed": [], "orphans": []}

    if not os.path.isdir(common):
        return result

    # 1) 收集所有安装清单
    installed_names = set()
    for acf in glob.glob(os.path.join(sa, "appmanifest_*.acf")):
        info = parse_acf(acf)
        appid = os.path.basename(acf).replace("appmanifest_", "").replace(".acf", "")
        if "installdir" in info:
            installed_names.add(info["installdir"].lower())
            result["installed"].append((appid, info.get("name", "?"),
                                        info["installdir"]))

    # 2) common 下的实际目录
    for entry in sorted(os.scandir(common), key=lambda e: e.name.lower()):
        if not entry.is_dir():
            continue
        low = entry.name.lower()
        if low not in installed_names and low not in WHITELIST:
            result["orphans"].append(entry.path)
    return result

def main():
    args = sys.argv[1:]
    want_delete = "--delete" in args
    auto_yes = "--yes" in args

    steam = get_steam_path()
    if not steam:
        print("[错误] 找不到 Steam 安装目录(注册表 HKCU\\Software\\Valve\\Steam)")
        sys.exit(1)
    print(f"[信息] Steam 安装目录: {steam}\n")

    all_orphans = []   # (库路径, 孤儿目录)
    for lib in get_library_folders(steam):
        res = scan_library(lib)
        sa = os.path.join(lib, "steamapps")
        print(f"===== 游戏库: {lib} =====")
        if res["installed"]:
            print(f"  在册游戏 {len(res['installed'])} 个:")
            for appid, name, _ in res["installed"]:
                print(f"    [已安装] {name}  (appid={appid})")
        else:
            print("  [在册游戏] 无")
        print()
        for p in res["orphans"]:
            all_orphans.append((lib, p))
    print(f"===== 扫描完成:发现 {len(all_orphans)} 个孤儿目录 =====\n")

    if not all_orphans:
        print("🎉 没有孤儿残留,硬盘很干净!")
        return

    # 孤儿明细(统计大小较慢,逐个来)
    details = []
    for lib, p in all_orphans:
        print(f"[孤儿] {p}")
        size = dir_size(p)
        saves = find_save_dirs(p)
        flag = "  ⚠️ 含存档/配置子目录!" if saves else ""
        print(f"        大小: {human(size)}  最后修改: "
              f"{os.path.getmtime(p) and __import__('datetime').datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d}{flag}")
        if saves:
            for s in saves[:5]:
                print(f"          └ 存档: {s}")
        details.append((p, size, saves))

    if not want_delete:
        print("\n(仅扫描模式。加 --delete 可交互删除,加 --yes 自动删除)")
        return

    print("\n===== 删除阶段 =====")
    for p, size, saves in details:
        if saves and not auto_yes:
            print(f"⏭️  跳过(含存档,需先手动备份): {p}")
            continue
        if not auto_yes:
            ans = input(f"删除 {p} ({human(size)}) ? [y/N] ").strip().lower()
            if ans != "y":
                print("跳过")
                continue
        print(f"🗑️  删除中: {p} ...")
        try:
            shutil.rmtree(p)
            print(f"   ✅ 已删除,释放 {human(size)}")
        except OSError as e:
            print(f"   ❌ 失败: {e}")

    print("\n完成!")

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
