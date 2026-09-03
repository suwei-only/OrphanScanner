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
        self.assertEqual(cfg.SPONSOR_URL, "")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
