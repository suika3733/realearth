"""历史回填 - 从 RAMMB 40 天窗口补齐历史影像

数据源: latest_times_5760.json（5760 个时间戳, 10 分钟间隔, 约 40 天窗口）
流程: 拉取全量时间戳 -> 按目标日期筛选 -> 逐帧下载拼接 -> archive_frame 归档
断点续传: archive_frame 幂等, 已存在的帧自动跳过, 可安全重跑
"""
import datetime
import json
import logging
import time
import urllib.request

from config import load_config
from providers.geostationary import fetch_satellite_image
from archive import archive_frame, utc_to_local, TIMELAPSE_DIR
from tasks import TaskManager

logger = logging.getLogger(__name__)

RAMMB_BASE = "https://rammb-slider.cira.colostate.edu"
RETRY = 2
SLEEP_BETWEEN = 0.2


def _load_remote_times(satellite: str, color: str) -> list:
    """拉取 RAMMB 40 天窗口全量时间戳（降序）"""
    url = f"{RAMMB_BASE}/data/json/{satellite}/full_disk/{color}/latest_times_5760.json"
    with urllib.request.urlopen(url, timeout=30) as f:
        data = json.load(f)
    return [int(t) for t in data.get("timestamps_int", [])]


def _iter_days(start: str, end: str):
    """遍历【UTC 日期】区间。

    用户输入的日期范围是本地日期；本地 00:00-07:59 的影像对应 UTC 前一天的
    16:00-23:50，因此从 start 前一天开始拉取（归档时按本地日期归位，多余的
    帧会自然落入前一天目录，幂等可重跑）。
    """
    d = datetime.date.fromisoformat(start) - datetime.timedelta(days=1)
    e = datetime.date.fromisoformat(end)
    while d <= e:
        yield d
        d += datetime.timedelta(days=1)


def _fmt_time(tc) -> str:
    """time_code -> 本地 HH:MM"""
    _, lt = utc_to_local(str(tc))
    return f"{lt[:2]}:{lt[2:4]}"


def list_remote_times(satellite: str, color: str, day) -> list:
    """查询 RAMMB 某天可用时间戳列表（升序）"""
    ts = _load_remote_times(satellite, color)
    prefix = day.strftime("%Y%m%d")
    return sorted(t for t in ts if str(t).startswith(prefix))


def backfill(satellite, start, end, color=None, target_size=None, task=None):
    """后台任务：回填 [start, end] 日期段全部帧。

    进度经 TaskManager.report 上报；task["cancel"] 置位时优雅退出。
    """
    cfg = load_config()
    color = color or cfg.get("satellite_color", "natural_color")
    target_size = target_size or cfg.get("satellite_size", 1080)

    # 1) 收集目标时间码
    all_codes = []
    for day in _iter_days(start, end):
        if task and TaskManager.is_canceled(task):
            return
        try:
            codes = list_remote_times(satellite, color, day)
        except Exception as e:
            logger.warning(f"[backfill] list times {day} failed: {e}")
            codes = []
        all_codes.extend((day, c) for c in codes)
        TaskManager.report(task, msg=f"扫描远端 {day} ...")

    total = len(all_codes)
    if total == 0:
        TaskManager.report(task, current=0, total=0,
                           msg="该日期段远端无可用影像（仅支持最近约 40 天）")
        return
    TaskManager.report(task, current=0, total=total, msg=f"共 {total} 帧待回填")

    done = 0
    skipped = 0
    failed = 0
    for day, tc in all_codes:
        if TaskManager.is_canceled(task):
            raise RuntimeError("已取消")
        # 归档目录按本地日期（与 archive_frame 一致）
        date, _ = utc_to_local(str(tc))
        # 已归档则跳过（断点续传）
        if (TIMELAPSE_DIR / satellite / date / f"{tc}.jpg").exists():
            skipped += 1
            done += 1
            TaskManager.report(task, current=done, total=total,
                               msg=f"跳过已有 {date} {_fmt_time(tc)}")
            continue
        ok = False
        for attempt in range(RETRY + 1):
            try:
                path = fetch_satellite_image(
                    satellite=satellite, color=color,
                    target_size=target_size, time_code=tc)
                if path:
                    archive_frame(satellite, path, time_code=tc)
                    ok = True
                    break
            except Exception as e:
                logger.warning(f"[backfill] frame {tc} attempt {attempt}: {e}")
            time.sleep(SLEEP_BETWEEN)
        done += 1
        if ok:
            TaskManager.report(task, current=done, total=total,
                               msg=f"已回填 {date} {_fmt_time(tc)}")
        else:
            failed += 1
            TaskManager.report(task, current=done, total=total,
                               msg=f"帧失败 {date} {_fmt_time(tc)}")
    TaskManager.report(task, current=done, total=total,
                       msg=f"回填完成: 新增 {done - skipped} / 已有 {skipped} / 失败 {failed}")
