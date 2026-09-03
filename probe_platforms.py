#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测本机:可用的"软件清单来源" + 注册表卸载项健康度"""
import os, sys, winreg

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def enum_uninstall(root, path):
    out = []
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
                name = ""
                loc = ""
                try:
                    name, _ = winreg.QueryValueEx(sk, "DisplayName")
                except OSError:
                    pass
                try:
                    loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                except OSError:
                    pass
                out.append((name, loc))
            except OSError:
                pass
    except OSError:
        pass
    return out

rows = []
for root, label in [(winreg.HKEY_LOCAL_MACHINE, "HKLM"),
                    (winreg.HKEY_CURRENT_USER, "HKCU")]:
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    rows += [(label, *r) for r in enum_uninstall(root, base)]
    if root == winreg.HKEY_LOCAL_MACHINE:
        rows += [("HKLM32", *r) for r in enum_uninstall(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]

print(f"注册表卸载项总数: {len(rows)}")
print()

# 1) 失效项:有 InstallLocation 但目录不存在(卸载残留的注册表项)
dead = [(label, n, l) for label, n, l in rows if l and not os.path.isdir(l)]
print(f"== 高置信残留:注册表登记但安装目录已不存在: {len(dead)} 项 ==")
for label, n, l in dead[:25]:
    print(f"  [{label}] {n or '(无名)'}\n      -> {l}")
print()

# 2) 游戏平台痕迹
print("== 游戏平台痕迹 ==")
platforms = {
    "Epic": [r"C:\Program Files (x86)\Epic Games", r"C:\Program Files\Epic Games",
             os.path.expandvars(r"%LOCALAPPDATA%\EpicGamesLauncher")],
    "GOG": [r"C:\Program Files (x86)\GOG Galaxy", os.path.expandvars(r"%PROGRAMDATA%\GOG.com")],
    "Battle.net": [r"C:\Program Files (x86)\Battle.net"],
    "WeGame": [r"C:\Program Files (x86)\WeGame", r"C:\Program Files\WeGame"],
    "EA app": [r"C:\Program Files\EA Games", os.path.expandvars(r"%LOCALAPPDATA%\Electronic Arts")],
    "Ubisoft": [r"C:\Program Files (x86)\Ubisoft", r"C:\Program Files\Ubisoft"],
    "Riot": [r"C:\Riot Games"],
}
for name, paths in platforms.items():
    hit = [p for p in paths if os.path.isdir(p)]
    print(f"  {name}: {'找到 ' + ', '.join(hit) if hit else '未安装'}")

# 3) 卸载项里带 InstallLocation 的样例(看数据质量)
with_loc = [(n, l) for _, n, l in rows if l]
print(f"\n== 带 InstallLocation 的项: {len(with_loc)} 个(可用于反向检查) ==")
for n, l in with_loc[:10]:
    ok = "✔存在" if os.path.isdir(l) else "✘失效"
    print(f"  {ok}  {n or '(无名)'}  -> {l}")
