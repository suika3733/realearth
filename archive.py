"""时间流逝 - 卫星影像归档模块

把用户选定卫星的每次获取影像按天归档到 ~/.nasa_wallpaper/timelapse/，
与现有 cache/ 解耦。帧文件名 = RAMMB time_code（YYYYMMDDHHMMSS），
天然有序、天然去重（同 time_code 只存一份）。

设计要点：
- 归档是旁路动作，失败绝不抛出（调用方 try/except 已兜底，本模块内部也兜底）
- 白名单过滤：只有 config["timelapse_archive_sats"] 里的卫星才归档
- 幂等：目标文件已存在则跳过，重复获取/回填不产生重复帧
"""
import logging
import re
import shutil
from pathlib import Path

from config import TIMELAPSE_DIR, load_config, save_config

logger = logging.getLogger(__name__)

DATE_RE = re.compile(r"(\d{14})")


def parse_time_code(filename: str):
    """从缓存文件名解析 time_code。

    RAMMB 缓存文件名形如: himawari_natural_color_3_20260824064000.jpg
    Returns: (time_code, date_YYYYMMDD, time_HHMMSS) 或 (None, None, None)
    """
    m = DATE_RE.search(filename)
    if not m:
        return None, None, None
    tc = m.group(1)
    return tc, tc[:8], tc[8:]


def _sat_dir(satellite: str) -> Path:
    return TIMELAPSE_DIR / satellite


def _day_dir(satellite: str, date: str) -> Path:
    """date 形如 2026-08-24"""
    return _sat_dir(satellite) / date


def archive_enabled(satellite: str) -> bool:
    cfg = load_config()
    return satellite in cfg.get("timelapse_archive_sats", [])


def get_archive_sats() -> list:
    cfg = load_config()
    return list(cfg.get("timelapse_archive_sats", []))


def set_archive_sat(satellite: str, on: bool) -> dict:
    cfg = load_config()
    sats = list(cfg.get("timelapse_archive_sats", []))
    if on and satellite not in sats:
        sats.append(satellite)
    elif not on and satellite in sats:
        sats.remove(satellite)
    cfg["timelapse_archive_sats"] = sats
    save_config(cfg)
    return {"ok": True, "archive_sats": sats}


def archive_frame(satellite: str, image_path, time_code=None) -> dict:
    """归档一帧影像。幂等：已存在则跳过。

    Args:
        satellite: 卫星 id（如 himawari）
        image_path: 缓存影像路径（文件名含 time_code 时可省略 time_code 参数）
        time_code: 可选，14 位时间码 YYYYMMDDHHMMSS

    Returns:
        {ok, path, is_new, sat, date, time, reason}
    """
    try:
        if not archive_enabled(satellite):
            return {"ok": False, "is_new": False, "reason": "not_in_whitelist"}
        src = Path(image_path)
        if not src.exists():
            return {"ok": False, "is_new": False, "reason": "src_missing"}
        if not time_code:
            time_code, date8, time6 = parse_time_code(src.name)
            if not time_code:
                return {"ok": False, "is_new": False, "reason": "no_time_code"}
        else:
            time_code = str(time_code)
            date8, time6 = time_code[:8], time_code[8:]
        date = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
        dst_dir = _day_dir(satellite, date)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{time_code}.jpg"
        if dst.exists():
            return {"ok": True, "path": str(dst), "is_new": False,
                    "sat": satellite, "date": date, "time": time6}
        shutil.copy2(src, dst)
        return {"ok": True, "path": str(dst), "is_new": True,
                "sat": satellite, "date": date, "time": time6}
    except Exception as e:
        logger.error(f"archive_frame error ({satellite} {image_path}): {e}")
        return {"ok": False, "is_new": False, "reason": str(e)}


def list_days(satellite: str) -> list:
    """返回该卫星全部有数据的日期: [{date, frames, size_mb, first, last}]"""
    sd = _sat_dir(satellite)
    out = []
    if sd.exists():
        for d in sorted(sd.iterdir()):
            if not d.is_dir():
                continue
            frames = sorted(d.glob("*.jpg"))
            if not frames:
                continue
            size = sum(f.stat().st_size for f in frames)
            out.append({
                "date": d.name,
                "frames": len(frames),
                "size_mb": round(size / 1048576, 2),
                "first": frames[0].stem,
                "last": frames[-1].stem,
            })
    return out


def list_frames(satellite: str, date: str) -> list:
    """返回某天全部帧: [{time, name, path, size}]，按时间升序"""
    dd = _day_dir(satellite, date)
    out = []
    if dd.exists():
        for f in sorted(dd.glob("*.jpg")):
            out.append({
                "time": f.stem,
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
            })
    return out


def delete_days(satellite: str, dates: list) -> int:
    """删除指定日期目录，返回删除帧数"""
    deleted = 0
    for date in dates:
        dd = _day_dir(satellite, date)
        if dd.exists():
            deleted += len(list(dd.glob("*.jpg")))
            shutil.rmtree(dd, ignore_errors=True)
    sd = _sat_dir(satellite)
    if sd.exists() and not any(sd.iterdir()):
        shutil.rmtree(sd, ignore_errors=True)
    return deleted


def storage_stats() -> dict:
    """按卫星/日期统计磁盘占用"""
    sats = []
    total_mb = 0.0
    total_frames = 0
    if TIMELAPSE_DIR.exists():
        for sd in sorted(TIMELAPSE_DIR.iterdir()):
            if not sd.is_dir():
                continue
            days = []
            sat_size = 0.0
            sat_frames = 0
            for d in sorted(sd.iterdir()):
                if not d.is_dir():
                    continue
                frames = sorted(d.glob("*.jpg"))
                if not frames:
                    continue
                size = sum(f.stat().st_size for f in frames) / 1048576
                days.append({"date": d.name, "frames": len(frames),
                             "size_mb": round(size, 2)})
                sat_size += size
                sat_frames += len(frames)
            if not days:
                continue
            sats.append({"id": sd.name, "days": days, "frames": sat_frames,
                         "size_mb": round(sat_size, 2)})
            total_mb += sat_size
            total_frames += sat_frames
    return {"sats": sats, "total_mb": round(total_mb, 2),
            "total_frames": total_frames}
