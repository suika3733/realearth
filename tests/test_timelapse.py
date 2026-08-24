"""时间流逝模块单元测试

运行: python -m tests.test_timelapse  (项目根目录)
覆盖: archive 归档/幂等/白名单/枚举/删除/统计, tasks 任务管理器,
      export GIF 导出, timelapse_player.FrameSource 扫描
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# 保证从项目根目录导入模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive
import export
from tasks import TaskManager
from timelapse_player import FrameSource
from PIL import Image

SAT = "himawari"
TC1 = "20260820060000"  # UTC 06:00
TC2 = "20260820061000"  # UTC 06:10
# 归档目录按本地日期分（archive.utc_to_local），测试动态计算避免时区假设
DATE, LOCAL_T1 = archive.utc_to_local(TC1)


def make_jpg(path: Path, color=(30, 60, 120), size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="tl_archive_"))
        # 重定向归档目录到临时目录, 不污染真实 ~/.nasa_wallpaper
        self._old_dir = archive.TIMELAPSE_DIR
        archive.TIMELAPSE_DIR = self._tmp
        # 备份真实白名单, 测试期间清空
        self._orig_sats = set(archive.get_archive_sats())
        for s in list(self._orig_sats):
            archive.set_archive_sat(s, False)

    def tearDown(self):
        archive.TIMELAPSE_DIR = self._old_dir
        # 恢复真实白名单
        current = set(archive.get_archive_sats())
        for s in self._orig_sats - current:
            archive.set_archive_sat(s, True)
        for s in current - self._orig_sats:
            archive.set_archive_sat(s, False)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _src(self, name="himawari_natural_color_3_20260820060000.jpg"):
        p = self._tmp / name
        make_jpg(p)
        return p

    def test_not_in_whitelist(self):
        r = archive.archive_frame(SAT, self._src())
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "not_in_whitelist")

    def test_archive_and_idempotent(self):
        archive.set_archive_sat(SAT, True)
        src = self._src()
        r1 = archive.archive_frame(SAT, src)
        self.assertTrue(r1["ok"])
        self.assertTrue(r1["is_new"])
        self.assertEqual(r1["sat"], SAT)
        self.assertEqual(r1["date"], DATE)
        self.assertEqual(r1["time"], LOCAL_T1)
        # 幂等: 再归档同 time_code 应跳过
        r2 = archive.archive_frame(SAT, src)
        self.assertTrue(r2["ok"])
        self.assertFalse(r2["is_new"])
        # 文件只存在一份
        day = archive.TIMELAPSE_DIR / SAT / DATE
        self.assertEqual(len(list(day.glob("*.jpg"))), 1)

    def test_parse_time_code(self):
        tc, d8, t6 = archive.parse_time_code("himawari_natural_color_3_20260820060000.jpg")
        self.assertEqual(tc, "20260820060000")
        self.assertEqual(d8, "20260820")
        self.assertEqual(t6, "060000")
        tc, _, _ = archive.parse_time_code("no_time.jpg")
        self.assertIsNone(tc)

    def test_utc_to_local(self):
        # 与 datetime 实现交叉验证（时区无关）
        import datetime as _dt
        utc = _dt.datetime(2026, 8, 20, 6, 0, 0, tzinfo=_dt.timezone.utc)
        loc = utc.astimezone()
        d, t = archive.utc_to_local("20260820060000")
        self.assertEqual(d, loc.strftime("%Y-%m-%d"))
        self.assertEqual(t, loc.strftime("%H%M%S"))

    def test_list_and_stats(self):
        archive.set_archive_sat(SAT, True)
        archive.archive_frame(SAT, self._src(f"a_20260820060000.jpg"))
        archive.archive_frame(SAT, self._src(f"b_20260820061000.jpg"))
        archive.archive_frame(SAT, self._src(f"c_20260821080000.jpg"))
        days = archive.list_days(SAT)
        self.assertEqual(len(days), 2)
        d1 = days[0]
        self.assertEqual(d1["date"], DATE)
        self.assertEqual(d1["frames"], 2)
        # 文件真实存在且有大小
        day_files = list((archive.TIMELAPSE_DIR / SAT / DATE).glob("*.jpg"))
        self.assertEqual(len(day_files), 2)
        self.assertGreater(day_files[0].stat().st_size, 0)
        frames = archive.list_frames(SAT, DATE)
        self.assertEqual([f["time"] for f in frames], [TC1, TC2])
        stats = archive.storage_stats()
        self.assertEqual(stats["total_frames"], 3)
        self.assertEqual(len(stats["sats"]), 1)
        self.assertEqual(stats["sats"][0]["id"], SAT)

    def test_delete_days(self):
        archive.set_archive_sat(SAT, True)
        archive.archive_frame(SAT, self._src(f"a_20260820060000.jpg"))
        n = archive.delete_days(SAT, [DATE])
        self.assertEqual(n, 1)
        self.assertEqual(archive.list_days(SAT), [])


class TaskManagerTest(unittest.TestCase):
    def test_progress_and_done(self):
        done = threading.Event()

        def fake(task=None):
            TaskManager.report(task, current=0, total=10, msg="start")
            for i in range(1, 11):
                TaskManager.report(task, current=i, total=10, msg=f"step {i}")
            done.set()

        tid = TaskManager.submit(fake)
        self.assertTrue(done.wait(5))
        p = TaskManager.progress(tid)
        self.assertTrue(p["found"])
        self.assertEqual(p["status"], "done")
        self.assertEqual(p["pct"], 100)

    def test_cancel(self):
        go = threading.Event()

        def fake(task=None):
            TaskManager.report(task, total=100, msg="running")
            while not TaskManager.is_canceled(task):
                go.set()
                time.sleep(0.01)

        tid = TaskManager.submit(fake)
        self.assertTrue(go.wait(5))
        TaskManager.cancel(tid)
        # 等待 canceling 状态落地
        for _ in range(100):
            if TaskManager.progress(tid)["status"] == "canceling":
                break
            time.sleep(0.02)
        p = TaskManager.progress(tid)
        self.assertEqual(p["status"], "canceling")


class ExportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="tl_export_"))
        self._old_dir = export.EXPORT_DIR
        export.EXPORT_DIR = self._tmp
        self._old_tl = archive.TIMELAPSE_DIR
        archive.TIMELAPSE_DIR = self._tmp / "timelapse"
        self._orig_sats = set(archive.get_archive_sats())
        archive.set_archive_sat(SAT, True)
        # 帧用不同颜色, 避免 Pillow 保存 GIF 时合并相同帧
        make_jpg(archive.TIMELAPSE_DIR / SAT / DATE / f"{TC1}.jpg", color=(30, 60, 120))
        make_jpg(archive.TIMELAPSE_DIR / SAT / DATE / f"{TC2}.jpg", color=(200, 120, 40))

    def tearDown(self):
        export.EXPORT_DIR = self._old_dir
        archive.TIMELAPSE_DIR = self._old_tl
        current = set(archive.get_archive_sats())
        for s in self._orig_sats - current:
            archive.set_archive_sat(s, True)
        for s in current - self._orig_sats:
            archive.set_archive_sat(s, False)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_gif_export(self):
        out = export.export_timelapse(SAT, DATE, DATE, fmt="gif", fps=10, interval=1)
        self.assertTrue(Path(out).exists())
        self.assertGreater(Path(out).stat().st_size, 0)
        with Image.open(out) as im:
            self.assertEqual(im.format, "GIF")
            self.assertEqual(im.n_frames, 2)

    def test_export_to_custom_path(self):
        out = export.export_timelapse(SAT, DATE, DATE, fmt="gif", fps=10,
                                      interval=1,
                                      out_path=str(self._tmp / "custom" / "my.gif"))
        self.assertTrue(Path(out).exists())
        self.assertEqual(Path(out).name, "my.gif")
        # 扩展名补齐: 无后缀时按 fmt 补
        out2 = export.export_timelapse(SAT, DATE, DATE, fmt="gif", fps=10,
                                       interval=1, out_path=str(self._tmp / "x"))
        self.assertEqual(Path(out2).name, "x.gif")

    def test_export_empty_range(self):
        with self.assertRaises(RuntimeError):
            export.export_timelapse(SAT, "2020-01-01", "2020-01-02", fmt="gif")


class FrameSourceTest(unittest.TestCase):
    def test_scan(self):
        tmp = Path(tempfile.mkdtemp(prefix="tl_fs_"))
        try:
            old = archive.TIMELAPSE_DIR
            archive.TIMELAPSE_DIR = tmp
            d = archive.TIMELAPSE_DIR / SAT / DATE
            make_jpg(d / "20260820060000.jpg")
            make_jpg(d / "20260820061000.jpg")
            fs = FrameSource(SAT, DATE)
            self.assertEqual(fs.list(), ["20260820060000.jpg", "20260820061000.jpg"])
            archive.TIMELAPSE_DIR = old
        finally:
            archive.TIMELAPSE_DIR = tmp.parent
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
