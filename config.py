"""配置管理模块"""
import json
import os
from pathlib import Path

APP_DIR = Path.home() / ".nasa_wallpaper"
CONFIG_FILE = APP_DIR / "config.json"
IMAGE_CACHE_DIR = APP_DIR / "cache"
WALLPAPER_DIR = APP_DIR / "wallpaper"
TIMELAPSE_DIR = APP_DIR / "timelapse"   # 时间流逝：按 卫星/日期 归档的帧序列
EXPORT_DIR = APP_DIR / "export"         # 时间流逝：导出的 MP4/GIF 输出目录

APOD_API_URL = "https://api.nasa.gov/planetary/apod"
DEFAULT_API_KEY = "DEMO_KEY"

DEFAULT_CONFIG = {
    "api_key": DEFAULT_API_KEY,
    "selected_category": "all",
    "auto_update": True,
    "update_time": "09:00",
    "last_update": None,
    "hd": True,
    "data_source": "apod",              # "apod" | "satellite" | "sdo"
    "satellite_id": "himawari",         # 默认卫星
    "satellite_color": "natural_color", # 颜色模式: natural_color | geocolor
    "satellite_size": 1080,             # 卫星图目标尺寸
    "satellite_auto_refresh": True,     # 卫星自动刷新
    "satellite_refresh_interval": 10,   # 卫星刷新间隔（分钟）
    "sdo_band": "0304",                 # SDO 波段
    "sdo_size": 2048,                   # SDO 目标尺寸
    "sdo_auto_refresh": True,           # SDO 自动刷新
    "sdo_refresh_interval": 60,         # SDO 刷新间隔（分钟）
    "earth_resolution": 2200,           # [兼容] Himawari-8 分辨率
    "earth_auto_refresh": True,         # [兼容]
    "earth_refresh_interval": 10,       # [兼容]
    "wallpaper_style": "fill",
    # 壁纸自定义位置与缩放（百分比，50/50=居中，100=原始大小）
    "wallpaper_pos_x": 50,          # 水平位置 0-100（0=最左，100=最右）
    "wallpaper_pos_y": 50,          # 垂直位置 0-100（0=最上，100=最下）
    "wallpaper_scale": 50,          # 缩放 0-100（50=原始大小，0=极小，100=放大2倍）
    # ===== 时间流逝模块 =====
    "timelapse_archive_sats": [],   # 归档白名单（空=不归档任何卫星；用户勾选后开启）
    "timelapse_live_sat": None,     # 动态壁纸卫星（None=未启用）
    "timelapse_live_date": None,    # 动态壁纸日期（None=跟随今天）
    "timelapse_fps": 10,            # 播放帧率
    "timelapse_low_fps": 3,         # 低帧数(<8)时降速
    "timelapse_scan_interval": 30,  # 新帧扫描间隔（秒）
    "timelapse_keep_days": 0,       # 0=永久保留；N=自动清理 N 天前
    "timelapse_export_dir": "",     # 空= ~/.nasa_wallpaper/export
}

# 壁纸样式映射（注册表值）
WALLPAPER_STYLES = {
    "center":  {"WallpaperStyle": "0", "TileWallpaper": "0"},
    "tile":    {"WallpaperStyle": "0", "TileWallpaper": "1"},
    "stretch": {"WallpaperStyle": "2", "TileWallpaper": "0"},
    "fit":     {"WallpaperStyle": "6", "TileWallpaper": "0"},
    "fill":    {"WallpaperStyle": "10", "TileWallpaper": "0"},
}

CATEGORIES = {
    "nebula": {
        "name": "星云",
        "keywords": ["nebula", "nebulae", "ngc", "ic ", "messa", "supernova remnant",
                      "emission", "reflection nebula", "planetary nebula", "dark nebula"],
    },
    "galaxy": {
        "name": "星系",
        "keywords": ["galaxy", "galaxies", "spiral", "andromeda", "milky way",
                      "magellanic", "cluster of galaxies", "dwarf galaxy"],
    },
    "planet": {
        "name": "行星",
        "keywords": ["jupiter", "saturn", "mars", "venus", "mercury",
                      "neptune", "uranus", "pluto", "planet", "gas giant"],
    },
    "earth": {
        "name": "地球",
        "keywords": ["earth", "blue marble", "dscovr", "epic", "aurora",
                      "hurricane", "cloud", "ocean", "atmosphere", "terra", "aqua"],
    },
    "sun": {
        "name": "太阳",
        "keywords": ["sun", "solar", "corona", "sunspot", "solar flare",
                      "eclipse", "helio", "sdo"],
    },
    "moon": {
        "name": "月球",
        "keywords": ["moon", "lunar", "crescent", "full moon", "eclipse moon",
                      "moonrise", "moonset"],
    },
    "aurora": {
        "name": "极光",
        "keywords": ["aurora", "northern lights", "southern lights",
                      "aurora borealis", "aurora australis"],
    },
    "comet": {
        "name": "彗星与小天体",
        "keywords": ["comet", "asteroid", "meteor", "shooting star",
                      "leonid", "perseid", "geminid", "fireball"],
    },
    "stars": {
        "name": "恒星与星团",
        "keywords": ["star cluster", "globular", "open cluster",
                      "pleiades", "binary star", "white dwarf", "neutron star",
                      "constellation", "star trail"],
    },
    "iss": {
        "name": "空间站与航天器",
        "keywords": ["iss", "space station", "shuttle", "apollo",
                      "astronaut", "spacewalk", "rocket", "launch",
                      "telescope", "hubble", "jwst", "james webb"],
    },
    "live_earth": {
        "name": "实时地球",
        "keywords": ["__himawari__"],  # 内部标记，不参与关键词匹配
    },
}

ALL_CATEGORY = "all"


def ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)
    TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            merged = {**DEFAULT_CONFIG, **config}
            # 兼容旧版壁纸缩放：旧范围 50-200 以 100 为原始大小；新版 0-100 以 50 为原始大小
            old_scale = merged.get("wallpaper_scale", 50)
            if old_scale > 100:
                merged["wallpaper_scale"] = max(0, min(100, int(old_scale / 2)))
            return merged
        except (json.JSONDecodeError, IOError):
            pass
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_image_cache_path(date_str: str, hd: bool = False) -> Path:
    ext = ".jpg"
    suffix = "_hd" if hd else ""
    return IMAGE_CACHE_DIR / f"{date_str}{suffix}{ext}"


def get_wallpaper_path(date_str: str) -> Path:
    return WALLPAPER_DIR / f"wallpaper_{date_str}.jpg"


def get_metadata_path() -> Path:
    return APP_DIR / "metadata.json"


def load_metadata() -> dict:
    path = get_metadata_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"images": {}}


def save_metadata(metadata: dict):
    path = get_metadata_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
