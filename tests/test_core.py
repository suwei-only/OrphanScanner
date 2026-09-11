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


if __name__ == "__main__":
    unittest.main(verbosity=2)
