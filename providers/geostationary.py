"""地球静止卫星图像获取 — 基于 CIRA RAMMB-Slider

数据来源: https://rammb-slider.cira.colostate.edu
支持卫星: GOES-16/18, Himawari-8, GK2A, Meteosat-9/0deg
"""

import datetime
import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from config import IMAGE_CACHE_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 卫星元数据
# ---------------------------------------------------------------------------
GEOSTATIONARY_SATELLITES = {
    "goes-16":      {"name": "GOES-16 (美洲)",      "size": 678, "region": "americas"},
    "goes-18":      {"name": "GOES-18 (美洲西)",     "size": 678, "region": "americas"},
    "himawari":     {"name": "Himawari-8 (亚太)",   "size": 688, "region": "asia_pacific"},
    "gk2a":         {"name": "GK2A (韩国)",          "size": 688, "region": "asia_pacific"},
    "meteosat-0deg": {"name": "Meteosat 0° (欧/非)", "size": 464, "region": "europe_africa"},
    "meteosat-9":   {"name": "Meteosat-9 (印度洋)",  "size": 464, "region": "indian_ocean"},
}

SATELLITE_SIZES = {k: v["size"] for k, v in GEOSTATIONARY_SATELLITES.items()}

COLOR_MODES = {
    "natural_color": "自然色",
    "geocolor":      "地球色 (含夜景)",
}

RAMMB_BASE = "https://rammb-slider.cira.colostate.edu"

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
SATELLITE_CACHE_DIR = IMAGE_CACHE_DIR / "satellite"


def _get_time_code(satellite: str, color: str) -> tuple[int, str]:
    """获取最新可用时间戳"""
    url = f"{RAMMB_BASE}/data/json/{satellite}/full_disk/{color}/latest_times.json"
    with urllib.request.urlopen(url, timeout=15) as f:
        data = json.load(f)
    latest = data["timestamps_int"][0]
    date = datetime.datetime.strptime(str(latest), "%Y%m%d%H%M%S").strftime("%Y/%m/%d")
    return latest, date


def _calc_scale(satellite: str, target_size: int) -> int:
    """计算缩放级别 (0-4)"""
    base = SATELLITE_SIZES[satellite]
    ratio = target_size / base / 1.2
    scale = int(ratio).bit_length()  # log2 取整
    scale = max(0, min(scale, 4))
    if satellite.startswith("meteosat") and scale == 4:
        scale = 3  # Meteosat 最大 8 倍
    return scale


def _build_url(satellite: str, scale: int, color: str, time_code: int) -> str:
    """构建指定时刻的瓦片基础 URL"""
    date = datetime.datetime.strptime(str(time_code), "%Y%m%d%H%M%S").strftime("%Y/%m/%d")
    return f"{RAMMB_BASE}/data/imagery/{date}/{satellite}---full_disk/{color}/{time_code}/0{scale}"


def _download_tile(url: str) -> Image.Image:
    """下载单个瓦片"""
    import requests
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    from io import BytesIO
    return Image.open(BytesIO(resp.content))


def _fetch_and_compose(satellite: str, color: str, scale: int,
                       time_code: int, force: bool = False) -> str | None:
    """下载指定时刻的瓦片并拼接为整盘影像，返回缓存路径。

    cache 路径 = SATELLITE_CACHE_DIR/{satellite}_{color}_{scale}_{time_code}.jpg
    """
    base_url = _build_url(satellite, scale, color, time_code)

    SATELLITE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = f"{satellite}_{color}_{scale}_{time_code}"
    cache_path = SATELLITE_CACHE_DIR / f"{cache_key}.jpg"

    if not force and cache_path.exists():
        logger.info(f"Using cached: {cache_path}")
        return str(cache_path)

    # 瓦片数量: 2^scale x 2^scale
    tiles_n = 2 ** scale
    tilesize = SATELLITE_SIZES[satellite]

    logger.info(f"Fetching {satellite} ({color}) t={time_code}, scale={scale}, "
                f"{tiles_n}x{tiles_n} tiles")

    # 并行下载所有瓦片
    tile_map: dict[tuple[int, int], Image.Image] = {}

    def _fetch(row: int, col: int):
        url = f"{base_url}/{str(row).zfill(3)}_{str(col).zfill(3)}.png"
        img = _download_tile(url)
        return (row, col), img

    with ThreadPoolExecutor(max_workers=min(tiles_n * tiles_n, 16)) as pool:
        futures = {pool.submit(_fetch, r, c): (r, c)
                   for r in range(tiles_n) for c in range(tiles_n)}
        for future in as_completed(futures):
            try:
                pos, img = future.result()
                tile_map[pos] = img
            except Exception as e:
                logger.warning(f"Tile download failed: {e}")

    if not tile_map:
        logger.error("All tile downloads failed")
        return None

    # 拼接瓦片
    full_w = tilesize * tiles_n
    full_h = tilesize * tiles_n
    canvas = Image.new("RGB", (full_w, full_h))

    for (r, c), img in tile_map.items():
        x = c * tilesize
        y = r * tilesize
        canvas.paste(img, (x, y))

    canvas.save(str(cache_path), "JPEG", quality=94)
    logger.info(f"Saved: {cache_path} ({full_w}x{full_h})")
    return str(cache_path)


def fetch_satellite_image(
    satellite: str = "himawari",
    color: str = "natural_color",
    target_size: int = 1080,
    force: bool = False,
    time_code: int | None = None,
) -> str | None:
    """获取地球静止卫星合成图像

    Args:
        satellite: 卫星标识 (goes-16/goes-18/himawari/gk2a/meteosat-0deg/meteosat-9)
        color: 颜色模式 (natural_color / geocolor)
        target_size: 目标尺寸（像素），自动计算缩放级别
        force: 强制重新下载，忽略缓存
        time_code: 指定时刻 (YYYYMMDDHHMMSS)；缺省 = 最新可用时刻

    Returns:
        图像文件路径，失败返回 None
    """
    if satellite not in SATELLITE_SIZES:
        logger.error(f"Unknown satellite: {satellite}")
        return None

    if color not in COLOR_MODES:
        logger.error(f"Unknown color mode: {color}")
        return None

    try:
        scale = _calc_scale(satellite, target_size)
        if time_code is None:
            time_code, _date = _get_time_code(satellite, color)
        return _fetch_and_compose(satellite, color, scale, time_code, force=force)
    except Exception as e:
        logger.error(f"fetch_satellite_image error: {e}")
        return None
