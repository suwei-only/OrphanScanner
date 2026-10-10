#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OrphanScanner 核心逻辑测试(unittest,Windows 专属)。
运行:  python -m unittest discover -s tests -v
注意:  不发送真实邮件、不触碰 HKLM、不操作桌面文件。
"""
import os
import sys
import glob
import json
import shutil
import tempfile
import unittest
import winreg

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

import orphan_scanner as o            # noqa: E402
import feedback_config as cfg         # noqa: E402

TRASH_CLEANUP = ["C:\\.OrphanTrash", "D:\\.OrphanTrash"]


def _purge_trash():
    for t in TRASH_CLEANUP:
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)


def _reload_cfg():
    for m in [m for m in list(sys.modules)
              if m.startswith("feedback_config")]:
        del sys.modules[m]


class TestTrash(unittest.TestCase):
    """回收站:移入 / 还原 / 永久删除"""

    def setUp(self):
        _purge_trash()
        self.src = os.path.join(tempfile.mkdtemp(), "hv_src")
        os.makedirs(os.path.join(self.src, "sub"), exist_ok=True)
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("hi")

    def tearDown(self):
        if os.path.isdir(self.src):
            shutil.rmtree(self.src, ignore_errors=True)
        _purge_trash()

    def test_move_restore_purge(self):
        ok, _, meta = o.move_to_trash(self.src)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.src))
        self.assertEqual(len(o.trash_items()), 1)

        ok, _ = o.restore_item(meta)
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "a.txt")))
        self.assertEqual(len(o.trash_items()), 0)

        ok, _, meta2 = o.move_to_trash(self.src)
        self.assertTrue(ok)
        ok, _ = o.purge_item(meta2)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.src))
        self.assertEqual(len(o.trash_items()), 0)


class TestRegistryBackup(unittest.TestCase):
    """HKCU 注册表键备份 -> 删除 -> 还原(无 UAC)"""

    KREL = r"Software\OrphanScannerUnitTest"

    def setUp(self):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KREL)
        except OSError:
            pass
        _purge_trash()

    def tearDown(self):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KREL)
        except OSError:
            pass
        _purge_trash()

    def test_backup_restore(self):
        kpath = "HKEY_CURRENT_USER\\" + self.KREL
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.KREL)
        winreg.SetValueEx(k, "demo", 0, winreg.REG_SZ, "1")
        k.Close()
        ok, _, meta = o.backup_reg_key(kpath)
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(meta["moved_to"]))
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KREL)
        ok, _ = o.restore_item(meta)
        self.assertTrue(ok)
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KREL).Close()


class TestDiagnostics(unittest.TestCase):
    def test_system_diag(self):
        d = o.system_diag("boom")
        self.assertIn("OrphanScanner", d)
        self.assertIn("版本: " + o.APP_VERSION, d)
        self.assertIn("boom", d)

    def test_log_error(self):
        lp = os.path.join(PROJ, "orphan_scanner_errors.log")
        if os.path.isfile(lp):
            os.remove(lp)
        o.log_error("unit-test-entry")
        self.assertTrue(os.path.isfile(lp))
        with open(lp, encoding="utf-8") as f:
            self.assertIn("unit-test-entry", f.read())
        os.remove(lp)


class TestConfig(unittest.TestCase):
    """配置加载:内嵌默认 + 外置优先 + 空授权码降级"""

    CFG = os.path.join(PROJ, "feedback_config.py")

    def setUp(self):
        self.bak = self.CFG + ".bak"
        shutil.copy(self.CFG, self.bak)

    def tearDown(self):
        if os.path.isfile(self.bak):
            shutil.move(self.bak, self.CFG)
        _reload_cfg()

    def test_default_fields(self):
        self.assertEqual(cfg.SPONSOR_URL, "https://afdian.com/a/qt97205058")
        self.assertEqual(cfg.FEEDBACK_QQ, "2086047945")
        self.assertEqual(cfg.FEEDBACK_AUTH_CODE, "elfoqmgbuuczbiia")

    def test_external_priority(self):
        with open(self.CFG, "w", encoding="utf-8") as f:
            f.write('SPONSOR_URL = "https://afdian.com/a/ut"\n'
                    'FEEDBACK_QQ = "2086047945"\n'
                    'FEEDBACK_AUTH_CODE = ""\n'
                    'FEEDBACK_TO = "2086047945@qq.com"\n'
                    'FEEDBACK_USER = "2086047945@qq.com"\n')
        _reload_cfg()
        c = o._load_cfg()
        self.assertEqual(c.SPONSOR_URL, "https://afdian.com/a/ut")

    def test_send_feedback_degrade_no_side_effect(self):
        with open(self.CFG, "w", encoding="utf-8") as f:
            f.write('SPONSOR_URL = ""\n'
                    'FEEDBACK_QQ = "2086047945"\n'
                    'FEEDBACK_AUTH_CODE = ""\n'
                    'FEEDBACK_TO = "2086047945@qq.com"\n'
                    'FEEDBACK_USER = "2086047945@qq.com"\n')
        _reload_cfg()
        ok, m = o.send_feedback("不应发送")
        self.assertFalse(ok)
        self.assertIn("授权码", m)


class TestGuiModule(unittest.TestCase):
    def test_import_and_methods(self):
        import importlib
        gui = importlib.import_module("orphan_gui")
        for m in ("_sponsor", "_feedback", "_open_trash",
                  "_trash_worker", "_refresh_trash_badge"):
            self.assertTrue(hasattr(gui.ScannerApp, m), m)
        with open(os.path.join(PROJ, "orphan_gui.py"),
                  encoding="utf-8") as f:
            src = f.read()
        for s in ("赞助开发者", "反馈问题", "移至回收站", "回收站",
                  "_load_cfg"):
            self.assertIn(s, src)


class TestIcon(unittest.TestCase):
    def test_icon_valid(self):
        from PIL import Image
        im = Image.open(os.path.join(PROJ, "icon.ico"))
        self.assertEqual(im.format, "ICO")
        self.assertGreaterEqual(im.size[0], 16)


class TestDiskMap(unittest.TestCase):
    """磁盘地图(treemap 可视化)测试"""

    def test_squarify_properties(self):
        """squarify:面积守恒、无重叠、无越界、键完整"""
        import disk_visualizer as dv
        items = [(f"i{n}", v) for n, v in
                 enumerate([100, 60, 40, 25, 20, 10, 8, 5, 3, 1])]
        rects = dv.squarify(items, 0, 0, 1000, 600)
        self.assertEqual(len(rects), len(items))
        # 面积守恒(允许 1% 容差)
        area = sum(r[3] * r[4] for r in rects)
        self.assertLess(abs(area - 600000) / 600000, 0.01)
        # 边界
        for k, x, y, w, h in rects:
            self.assertGreaterEqual(x, -1)
            self.assertGreaterEqual(y, -1)
            self.assertLessEqual(x + w, 1001)
            self.assertLessEqual(y + h, 601)
        # 键完整
        self.assertEqual({r[0] for r in rects}, {k for k, _ in items})

    def test_squarify_no_overlap(self):
        import disk_visualizer as dv
        items = [(f"i{n}", v) for n, v in
                 enumerate([791, 412, 301, 155, 99, 42, 17])]
        rects = dv.squarify(items, 0, 0, 800, 500)
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                ox = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                oy = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
                self.assertFalse(ox > 1 and oy > 1,
                                 f"重叠: {a[0]} vs {b[0]}")

    def test_measure_and_scan(self):
        """measure 与 scan_items 对小目录统计正确"""
        import disk_visualizer as dv
        tmp = tempfile.mkdtemp(prefix="dvtest_")
        try:
            with open(os.path.join(tmp, "a.bin"), "wb") as f:
                f.write(b"x" * 1234)
            os.makedirs(os.path.join(tmp, "sub"))
            with open(os.path.join(tmp, "sub", "b.bin"), "wb") as f:
                f.write(b"y" * 500)
            size = dv.measure(tmp)
            self.assertEqual(size, 1734)
            got = []
            dv.scan_items(tmp, lambda n, p, s, d: got.append((n, s, d)),
                          lambda c, t: None)
            names = {n for n, _, _ in got}
            self.assertIn("a.bin", names)
            self.assertIn("sub", names)
            total = sum(s for _, s, _ in got)
            self.assertEqual(total, 1734)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_human_format(self):
        import disk_visualizer as dv
        self.assertEqual(dv.human(512), "512 B")
        self.assertEqual(dv.human(2048), "2.0 KB")

    def test_tree_builder_structure(self):
        """TreeBuilder:目录/大文件/小文件聚合/大小汇总"""
        import disk_visualizer as dv
        tmp = tempfile.mkdtemp(prefix="dv3_")
        try:
            os.makedirs(os.path.join(tmp, "sub", "inner"))
            with open(os.path.join(tmp, "big.bin"), "wb") as f:
                f.write(b"x" * (6 * 1024 * 1024))
            for i in range(3):
                with open(os.path.join(tmp, f"small{i}.bin"), "wb") as f:
                    f.write(b"y" * 1000)
            with open(os.path.join(tmp, "sub", "inner", "inner.bin"),
                      "wb") as f:
                f.write(b"z" * (7 * 1024 * 1024))
            b = dv.TreeBuilder()
            root = b.build(tmp)
            self.assertEqual(root.size,
                             6 * 1024 * 1024 + 3000 + 7 * 1024 * 1024)
            rmap = {c.name: c for c in root.children}
            # 大文件单独节点
            self.assertEqual(rmap["big.bin"].size, 6 * 1024 * 1024)
            # 小文件聚合
            self.assertEqual(rmap["(3个小文件)"].size, 3000)
            self.assertTrue(rmap["(3个小文件)"].agg)
            # 嵌套结构
            sub = rmap["sub"]
            inner = sub.children[0]
            self.assertEqual(inner.name, "inner")
            self.assertEqual(inner.children[0].name, "inner.bin")
            self.assertEqual(inner.children[0].size, 7 * 1024 * 1024)
            # 计数
            self.assertEqual(b.dirs_seen, 3)
            self.assertEqual(b.files_seen, 5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tree_builder_budget(self):
        """TreeBuilder: max_files 预算截断保护"""
        import disk_visualizer as dv
        tmp = tempfile.mkdtemp(prefix="dv3b_")
        try:
            for i in range(10):
                with open(os.path.join(tmp, f"f{i}.bin"), "wb") as f:
                    f.write(b"x" * 100)
            b = dv.TreeBuilder(big_file=50, max_files=4)
            root = b.build(tmp)
            self.assertTrue(b.truncated)
            self.assertEqual(b.files_seen, 4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAppIdentity(unittest.TestCase):
    """应用归属识别(纯函数,注入 sources,零外部依赖)"""

    def test_kb_component_match(self):
        import app_identity as ai
        tmp = tempfile.mkdtemp(prefix="ai_")
        try:
            wc = os.path.join(tmp, "WeChat Files")
            os.makedirs(os.path.join(wc, "wxid_x"), exist_ok=True)
            disp, src = ai.identify(os.path.join(wc, "wxid_x"), sources=[])
            self.assertEqual(disp, "微信(腾讯)")
            self.assertEqual(src, "内置知识库")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_case_insensitive(self):
        import app_identity as ai
        tmp = tempfile.mkdtemp(prefix="ai_")
        try:
            wc = os.path.join(tmp, "wechat files")
            os.makedirs(wc, exist_ok=True)
            disp, _ = ai.identify(wc.upper(), sources=[])
            self.assertEqual(disp, "微信(腾讯)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prefix_deepest_match(self):
        import app_identity as ai
        srcs = [
            (os.path.normcase(r"C:\Apps"), "根应用", "注册表卸载项"),
            (os.path.normcase(r"C:\Apps\Sub"), "子应用", "注册表卸载项"),
        ]
        disp, _ = ai.identify(r"C:\Apps\Sub\bin\x.exe", sources=srcs)
        self.assertEqual(disp, "子应用")

    def test_source_priority_same_depth(self):
        import app_identity as ai
        srcs = [
            (os.path.normcase(r"C:\Apps"), "注册表应用", "注册表卸载项"),
            (os.path.normcase(r"C:\Apps"), "进程应用", "运行中的程序"),
        ]
        disp, src = ai.identify(r"C:\Apps\x.exe", sources=srcs)
        self.assertEqual((disp, src), ("注册表应用", "注册表卸载项"))

    def test_kb_loses_to_registry(self):
        import app_identity as ai
        tmp = tempfile.mkdtemp(prefix="ai_")
        try:
            wc = os.path.join(tmp, "WeChat Files")
            os.makedirs(wc, exist_ok=True)
            srcs = [(os.path.normcase(tmp), "临时工具", "注册表卸载项")]
            disp, src = ai.identify(wc, sources=srcs)
            self.assertEqual(disp, "临时工具")
            self.assertEqual(src, "注册表卸载项")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown(self):
        import app_identity as ai
        tmp = tempfile.mkdtemp(prefix="ai_unknown_")
        try:
            disp, src = ai.identify(tmp, sources=[])
            self.assertIsNone(disp)
            self.assertIsNone(src)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sources_cache(self):
        import app_identity as ai
        calls = []
        orig = ai._collect_sources

        def fake():
            calls.append(1)
            return []
        ai._collect_sources = fake
        try:
            ai.invalidate_cache()
            ai.get_sources()
            ai.get_sources()
            self.assertEqual(len(calls), 1)
            ai.invalidate_cache()
            ai.get_sources()
            self.assertEqual(len(calls), 2)
        finally:
            ai._collect_sources = orig
            ai.invalidate_cache()


class TestFullScan(unittest.TestCase):
    """全盘扫描底层:卷枚举 / NTFS 分配大小 / 硬链接文件ID"""

    def test_list_volumes(self):
        import full_scan as fs
        vols = fs.list_volumes()
        self.assertTrue(vols)
        letters = [v[0] for v in vols]
        self.assertIn("C", letters)
        for letter, label, free, total in vols:
            self.assertIsInstance(free, int)
            self.assertGreater(total, 0)
        frees = [v[2] for v in vols]
        self.assertEqual(frees, sorted(frees))   # 剩余最少在前

    def test_file_alloc_links(self):
        import full_scan as fs
        tmp = tempfile.mkdtemp(prefix="fs_")
        try:
            p = os.path.join(tmp, "a.bin")
            with open(p, "wb") as f:
                f.write(b"x" * 1234)
            got = fs.file_alloc_links(p)
            self.assertIsNotNone(got)
            alloc, nlink = got
            self.assertGreaterEqual(alloc, 1234)   # 簇对齐,分配>=表观
            self.assertEqual(nlink, 1)
            self.assertIsNone(
                fs.file_alloc_links(os.path.join(tmp, "nope.bin")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_file_id_hardlink(self):
        import full_scan as fs
        tmp = tempfile.mkdtemp(prefix="fs_")
        try:
            p1 = os.path.join(tmp, "orig.bin")
            p2 = os.path.join(tmp, "link.bin")
            with open(p1, "wb") as f:
                f.write(b"y" * 4096)
            try:
                os.link(p1, p2)
            except OSError:
                self.skipTest("文件系统不支持硬链接")
            self.assertTrue(os.path.samefile(p1, p2))
            id1, id2 = fs.get_file_id(p1), fs.get_file_id(p2)
            self.assertIsNotNone(id1)
            self.assertEqual(id1, id2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_alloc_supported(self):
        import full_scan as fs
        self.assertTrue(fs.alloc_supported("C:\\"))


class TestProtectedPath(unittest.TestCase):
    """系统目录拒删铁律(零副作用:只判定,不真删)"""

    def test_protected(self):
        for p in (r"C:\Windows", r"C:\Windows\System32\drivers",
                  r"D:\Program Files", r"C:\Program Files (x86)\App",
                  r"C:\ProgramData\X", r"E:\System Volume Information",
                  r"D:\$Recycle.Bin", r"C:\pagefile.sys",
                  r"C:\hiberfil.sys", r"C:\swapfile.sys"):
            self.assertTrue(o.is_protected_path(p), p)

    def test_not_protected(self):
        for p in (os.path.expanduser("~"), tempfile.gettempdir(),
                  r"D:\Games\MyGame", r"D:\windows_tools"):
            self.assertFalse(o.is_protected_path(p), p)

    def test_move_to_trash_rejects(self):
        ok, msg, meta = o.move_to_trash(r"C:\Windows")
        self.assertFalse(ok)
        self.assertIn("受保护", msg)
        self.assertIsNone(meta)


class TestMarkAbsorb(unittest.TestCase):
    """标记待删:目录吸收内部标记(不可反向)+ 新 TreeBuilder 能力"""

    def test_is_descendant(self):
        import disk_visualizer as dv
        root = dv.Node("r", "/r", 0, True)
        a = dv.Node("a", "/r/a", 0, True, root)
        b = dv.Node("b", "/r/a/b", 0, False, a)
        c = dv.Node("c", "/r/c", 0, False, root)
        self.assertTrue(dv.is_descendant(a, root))
        self.assertTrue(dv.is_descendant(b, a))
        self.assertTrue(dv.is_descendant(root, root))
        self.assertFalse(dv.is_descendant(c, a))

    def test_absorb_irreversible(self):
        import disk_visualizer as dv
        root = dv.Node("r", "/r", 0, True)
        a = dv.Node("a", "/r/a", 0, True, root)
        b = dv.Node("b", "/r/a/b", 0, False, a)
        c = dv.Node("c", "/r/c", 0, False, root)
        marked = [b, c]
        marked2 = dv.absorb_marked(marked, a)   # 标记目录 a → 吸收 b
        self.assertEqual(set(marked2), {c, a})
        m3 = dv.absorb_marked(marked2, a)       # 幂等
        self.assertEqual(m3, marked2)
        m4 = [m for m in marked2 if m is not a]  # 取消目录标记
        self.assertNotIn(b, m4)                  # 不可反向:不恢复内部项

    def test_treebuilder_alloc_and_dedupe(self):
        import disk_visualizer as dv
        tmp = tempfile.mkdtemp(prefix="dv15_")
        try:
            with open(os.path.join(tmp, "big.bin"), "wb") as f:
                f.write(b"x" * (6 * 1024 * 1024))
            try:
                os.link(os.path.join(tmp, "big.bin"),
                        os.path.join(tmp, "big_link.bin"))
            except OSError:
                self.skipTest("文件系统不支持硬链接")
            b = dv.TreeBuilder(use_alloc=True, dedupe_hardlinks=True)
            root = b.build(tmp)
            self.assertTrue(b.alloc_ok)
            # 去重:硬链接只计一次;分配大小簇对齐,允许略大于表观 6MB
            self.assertGreaterEqual(root.size, 6 * 1024 * 1024)
            self.assertLess(root.size, 6 * 1024 * 1024 + 1024 * 1024)
            self.assertGreaterEqual(b.dup_saved, 6 * 1024 * 1024)
            self.assertEqual(root.file_count, 1)
            self.assertGreater(root.mtime, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_treebuilder_missing_root_no_crash(self):
        import disk_visualizer as dv
        b = dv.TreeBuilder()
        root = b.build(tempfile.mkdtemp(prefix="dv_ok_") + "_nope")
        self.assertEqual(root.size, 0)
        self.assertTrue(b.skipped)   # 不存在的根被记录为跳过,不崩溃

    def test_diskmap_window_smoke(self):
        import time
        import tkinter as tk
        import disk_visualizer as dv
        tmp = tempfile.mkdtemp(prefix="dvwin_")
        try:
            with open(os.path.join(tmp, "a.bin"), "wb") as f:
                f.write(b"x" * 3000)
            root = tk.Tk()
            root.withdraw()
            try:
                win = dv.DiskMapWindow(root)
                win._start(tmp)
                t0 = time.time()
                while time.time() - t0 < 15 and not win._done_flag:
                    root.update()
                    time.sleep(0.05)
                root.update()
                self.assertTrue(win._done_flag)
                self.assertGreater(win._show_root.size, 0)
                self.assertTrue(win._rect_nodes)
                node = next((n for n, *_ in win._rect_nodes
                             if not n.agg and n.path), None)
                if node is not None:
                    win._pick = node
                    win._toggle_mark()
                    self.assertEqual(len(win._marked), 1)
                    self.assertIn(node, win._marked)
                win.destroy()
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
