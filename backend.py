"""RealEarth 后端业务逻辑 — 与 UI 框架完全解耦

供 Web 前端 (pywebview) 通过 Api 桥接类调用。
所有方法返回可被 JSON 序列化的 dict / list / str / bool / None。
图片以 data:image/jpeg;base64,... 形式返回, 避免本地文件路径依赖。
自动刷新倒计时通过 window.evaluate_js 反向推送前端。
"""
import base64
import io
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from config import (
    load_config, save_config, load_metadata, save_metadata,
    IMAGE_CACHE_DIR, DEFAULT_API_KEY, ALL_CATEGORY,
)
from nasa_api import fetch_apod_range, download_image, ApodImage
from categorizer import categorize_image, get_category_name, get_all_category_keys
from wallpaper import set_wallpaper, watermark_image
from scheduler import start_scheduler, stop_scheduler, is_scheduler_running, check_and_update
from providers import GEOSTATIONARY_SATELLITES, SDO_BANDS, fetch_satellite_image, fetch_sdo_image

logger = logging.getLogger(__name__)

SAT_REFRESH_MIN = 10
SDO_REFRESH_MIN = 60
APP_VERSION = "v2.0.1"

# 数据源强调色 (供前端动态注入, 规范 2.1.5)
ACCENTS = {
    "apod":      {"primary": "#7C5CFC", "light": "#9D7FFF", "glow": "rgba(124,92,252,0.25)"},
    "satellite": {"primary": "#00B4D8", "light": "#33C9E8", "glow": "rgba(0,180,216,0.25)"},
    "sdo":       {"primary": "#FF8C00", "light": "#FFAA33", "glow": "rgba(255,140,0,0.25)"},
    "fy4":       {"primary": "#E8453C", "light": "#FF6B60", "glow": "rgba(232,69,60,0.25)"},
}


class RealEarthBackend:
    def __init__(self):
        self.config = load_config()
        self.metadata = load_metadata()
        self.images_by_cat = {}
        self.current_cat = ALL_CATEGORY
        self.current_idx = 0
        self.current_image = None

        self.current_source = "apod"

        # 卫星
        self.satellite_id = self.config.get("satellite_id", "himawari")
        self.satellite_color = self.config.get("satellite_color", "natural_color")
        self.satellite_size = self.config.get("satellite_size", 1080)
        self.sat_image_path = None

        # SDO
        self.sdo_band = self.config.get("sdo_band", "0304")
        self.sdo_image_path = None

        # 自动刷新
        self.sat_auto_refresh = bool(self.config.get("satellite_auto_refresh", False))
        self.sdo_auto_refresh = bool(self.config.get("sdo_auto_refresh", False))
        self._sat_next_refresh = None
        self._sdo_next_refresh = None
        self._sat_timer = None
        self._sdo_timer = None

        self._window = None  # 由 Api 在窗口创建后赋值；下划线前缀避免 pywebview 递归暴露

        self.rebuild_category_data()

    # ------------------------------------------------------------------
    # 图片辅助
    # ------------------------------------------------------------------
    def _image_to_b64(self, path, max_edge=1200):
        try:
            img = Image.open(path)
            w, h = img.size
            ratio = min(1.0, max_edge / max(w, h))
            if ratio < 1.0:
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            logger.error(f"image to b64 failed: {e}")
            return None

    def _apod_cache_path(self, img):
        cache_path = IMAGE_CACHE_DIR / f"{img.date}.jpg"
        if not cache_path.exists() and getattr(img, "hdurl", None):
            cache_path = IMAGE_CACHE_DIR / f"{img.date}_hd.jpg"
        return cache_path

    def _eval_js(self, expr):
        if not self._window:
            return
        try:
            self._window.evaluate_js(expr)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 分类 / APOD
    # ------------------------------------------------------------------
    def rebuild_category_data(self):
        self.images_by_cat = {key: [] for key in get_all_category_keys()}
        images = self.metadata.get("images", {})
        for _date_str, data in images.items():
            try:
                img = ApodImage.from_dict(data)
            except Exception:
                continue
            cat = categorize_image(img)
            self.images_by_cat.setdefault(cat, []).append(img)
            self.images_by_cat[ALL_CATEGORY].append(img)
        for key in self.images_by_cat:
            self.images_by_cat[key].sort(key=lambda x: x.date, reverse=True)

    def get_categories(self):
        keys = get_all_category_keys()
        return [
            {
                "key": key,
                "name": get_category_name(key),
                "count": len(self.images_by_cat.get(key, [])),
                "selected": key == self.current_cat,
            }
            for key in keys
        ]

    def set_source(self, source):
        if source not in ("apod", "satellite", "sdo"):
            source = "apod"
        self.current_source = source
        return {"source": source, "status": self._status_text(source)}

    def select_category(self, key):
        if key not in self.images_by_cat:
            return self.get_current_apod()
        self.current_cat = key
        self.current_idx = 0
        return self.get_current_apod()

    def prev_image(self):
        if self.current_idx > 0:
            self.current_idx -= 1
        return self.get_current_apod()

    def next_image(self):
        images = self.images_by_cat.get(self.current_cat, [])
        if self.current_idx < len(images) - 1:
            self.current_idx += 1
        return self.get_current_apod()

    def get_current_apod(self):
        images = self.images_by_cat.get(self.current_cat, [])
        total = len(images)
        if total == 0:
            return {
                "title": "", "date": "", "info": "", "image": None,
                "idx": 0, "total": 0, "has_image": False, "placeholder": True,
            }
        self.current_idx = max(0, min(self.current_idx, total - 1))
        img = images[self.current_idx]
        self.current_image = img
        cache = self._apod_cache_path(img)
        image_b64 = None
        if cache.exists():
            image_b64 = self._image_to_b64(str(cache))
        else:
            try:
                p = download_image(img, hd=self.config.get("hd", True))
                if p:
                    image_b64 = self._image_to_b64(p)
            except Exception as e:
                logger.error(f"apod download failed: {e}")
        info = f"{img.date}    {img.title}"
        if getattr(img, "copyright", None):
            info += f"    © {img.copyright}"
        return {
            "title": img.title, "date": img.date, "info": info,
            "image": image_b64, "idx": self.current_idx + 1, "total": total,
            "has_image": image_b64 is not None, "placeholder": False,
        }

    def set_apod_wallpaper(self):
        if not self.current_image:
            return {"ok": False, "msg": "请先选择一张图片"}
        cache = self._apod_cache_path(self.current_image)
        if not cache.exists() or True:
            try:
                p = download_image(self.current_image, hd=self.config.get("hd", True))
                if p:
                    cache = Path(p)
            except Exception as e:
                logger.error(e)
        if not cache.exists():
            return {"ok": False, "msg": "图片尚未下载完成"}
        style = self.config.get("wallpaper_style", "fill")
        wp = watermark_image(
            str(cache),
            left_text="来源: NASA 每日天文图片 (APOD)",
            right_text=f"拍摄: {self.current_image.date} | {self.current_image.title}",
            output_key=f"apod_{self.current_image.date}",
        )
        if set_wallpaper(wp, self.current_image.date.replace("-", ""), style=style):
            return {"ok": True, "msg": f"壁纸已更换：{self.current_image.title}"}
        return {"ok": False, "msg": "壁纸设置失败"}

    def fetch_apod(self, days):
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            images = fetch_apod_range(
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                api_key=self.config.get("api_key"),
            )
            meta = load_metadata()
            for img in images:
                meta["images"][img.date] = img.to_dict()
            save_metadata(meta)
            self.metadata = meta
            self.rebuild_category_data()
            return {
                "ok": True, "count": len(images),
                "apod": self.get_current_apod(),
                "categories": self.get_categories(),
            }
        except Exception as e:
            logger.error(f"fetch apod error: {e}")
            return {"ok": False, "msg": f"获取失败: {e}"}

    def update_now(self):
        try:
            result = check_and_update()
            self.rebuild_category_data()
            return {
                "ok": True, "updated": result,
                "apod": self.get_current_apod(),
                "categories": self.get_categories(),
            }
        except Exception as e:
            logger.error(f"update error: {e}")
            return {"ok": False, "msg": f"更新失败: {e}"}

    # ------------------------------------------------------------------
    # 卫星影像
    # ------------------------------------------------------------------
    def get_satellites(self):
        sats = []
        for sid, info in GEOSTATIONARY_SATELLITES.items():
            sats.append({
                "id": sid,
                "name": info.get("name", sid),
                "color": info.get("color", "#00B4D8"),
                "agency": info.get("agency", ""),
                "region": info.get("region", "-"),
                "selected": sid == self.satellite_id,
            })
        return sats

    def set_satellite(self, sat_id):
        if sat_id in GEOSTATIONARY_SATELLITES:
            self.satellite_id = sat_id
            self.config["satellite_id"] = sat_id
            save_config(self.config)
        return self.get_sat_info()

    def set_sat_color(self, color):
        self.satellite_color = color
        self.config["satellite_color"] = color
        save_config(self.config)

    def set_sat_size(self, size):
        try:
            size = int(size)
        except ValueError:
            size = 1080
        self.satellite_size = size
        self.config["satellite_size"] = size
        save_config(self.config)

    def get_sat_info(self):
        info = GEOSTATIONARY_SATELLITES.get(self.satellite_id, {})
        return {
            "id": self.satellite_id,
            "name": info.get("name", self.satellite_id),
            "agency": info.get("agency", ""),
            "region": info.get("region", "-"),
            "color": self.satellite_color,
            "size": self.satellite_size,
            "color_label": "自然色" if self.satellite_color == "natural_color" else "地球色",
        }

    def fetch_satellite(self):
        sat = self.satellite_id
        color = self.satellite_color
        size = self.satellite_size
        name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
        self.config["satellite_id"] = sat
        self.config["satellite_color"] = color
        self.config["satellite_size"] = size
        save_config(self.config)
        try:
            path = fetch_satellite_image(satellite=sat, color=color, target_size=size)
        except Exception as e:
            logger.error(f"sat fetch error: {e}")
            return {"ok": False, "msg": f"获取失败: {e}"}
        if not path:
            return {"ok": False, "msg": "数据暂时不可用，请稍后重试"}
        self.sat_image_path = path
        now = datetime.now()
        image_b64 = self._image_to_b64(path)
        return {
            "ok": True, "image": image_b64,
            "title": name,
            "status": f"卫星影像已更新 | {name}",
            "meta": f"{now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
        }

    def set_sat_wallpaper(self):
        if not self.sat_image_path or not Path(self.sat_image_path).exists():
            return {"ok": False, "msg": "请先获取卫星影像"}
        name = GEOSTATIONARY_SATELLITES.get(self.satellite_id, {}).get("name", self.satellite_id)
        style = self.config.get("wallpaper_style", "fill")
        now = datetime.now()
        wp = watermark_image(
            self.sat_image_path,
            left_text=f"来源: {name}",
            right_text=f"拍摄时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
            output_key=f"sat_{self.satellite_id}",
        )
        if set_wallpaper(wp, f"sat_{self.satellite_id}", style=style):
            return {"ok": True, "msg": f"壁纸已设置 | {name} | 后台持续自动更新"}
        return {"ok": False, "msg": "壁纸设置失败"}

    # ------------------------------------------------------------------
    # SDO 太阳观测
    # ------------------------------------------------------------------
    def get_sdo_bands(self):
        bands = []
        for key, info in SDO_BANDS.items():
            bands.append({
                "key": key,
                "name": info.get("name", key),
                "wavelength": info.get("wavelength", ""),
                "selected": key == self.sdo_band,
            })
        return bands

    def set_sdo_band(self, band):
        if band in SDO_BANDS:
            self.sdo_band = band
            self.config["sdo_band"] = band
            save_config(self.config)
        return self.get_sdo_band_info()

    def get_sdo_band_info(self):
        info = SDO_BANDS.get(self.sdo_band, {})
        return {
            "key": self.sdo_band,
            "name": info.get("name", self.sdo_band),
            "wavelength": info.get("wavelength", ""),
        }

    def fetch_sdo(self):
        band = self.sdo_band
        name = SDO_BANDS.get(band, {}).get("name", band)
        self.config["sdo_band"] = band
        save_config(self.config)
        try:
            path = fetch_sdo_image(band=band)
        except Exception as e:
            logger.error(f"sdo fetch error: {e}")
            return {"ok": False, "msg": f"获取失败: {e}"}
        if not path:
            return {"ok": False, "msg": "NASA SDO 数据暂时不可用"}
        self.sdo_image_path = path
        now = datetime.now()
        image_b64 = self._image_to_b64(path)
        return {
            "ok": True, "image": image_b64,
            "title": name,
            "status": f"太阳图像已更新 | {name}",
            "meta": f"NASA SDO | {now.strftime('%Y-%m-%d %H:%M')}",
        }

    def set_sdo_wallpaper(self):
        if not self.sdo_image_path or not Path(self.sdo_image_path).exists():
            return {"ok": False, "msg": "请先获取太阳图像"}
        name = SDO_BANDS.get(self.sdo_band, {}).get("name", self.sdo_band)
        style = self.config.get("wallpaper_style", "fill")
        now = datetime.now()
        wp = watermark_image(
            self.sdo_image_path,
            left_text="来源: NASA SDO 太阳观测",
            right_text=f"波段: {name} | {now.strftime('%Y-%m-%d %H:%M')}",
            output_key=f"sdo_{self.sdo_band}",
        )
        if set_wallpaper(wp, f"sdo_{self.sdo_band}", style=style):
            return {"ok": True, "msg": f"壁纸已设置 | {name}"}
        return {"ok": False, "msg": "壁纸设置失败"}

    # ------------------------------------------------------------------
    # 自动刷新 (倒计时经 evaluate_js 推送前端)
    # ------------------------------------------------------------------
    def resume_auto_refresh(self):
        if self.sat_auto_refresh and self._window:
            self._start_sat_timer()
        if self.sdo_auto_refresh and self._window:
            self._start_sdo_timer()

    def toggle_sat_auto_refresh(self):
        self.sat_auto_refresh = not self.sat_auto_refresh
        self.config["satellite_auto_refresh"] = self.sat_auto_refresh
        save_config(self.config)
        if self.sat_auto_refresh:
            self._start_sat_timer()
        else:
            self._stop_sat_timer()
        return {"on": self.sat_auto_refresh}

    def _start_sat_timer(self):
        self._stop_sat_timer()
        self._sat_next_refresh = datetime.now() + timedelta(minutes=SAT_REFRESH_MIN)
        self._sat_timer = threading.Timer(1.0, self._sat_countdown_loop)
        self._sat_timer.daemon = True
        self._sat_timer.start()

    def _stop_sat_timer(self):
        if self._sat_timer:
            self._sat_timer.cancel()
            self._sat_timer = None
        self._sat_next_refresh = None

    def _sat_countdown_loop(self):
        if not self.sat_auto_refresh or not self._window:
            return
        try:
            now = datetime.now()
            if self._sat_next_refresh and (self._sat_next_refresh - now).total_seconds() <= 0:
                self._sat_next_refresh = now + timedelta(minutes=SAT_REFRESH_MIN)
                self._eval_js("window.onSatRefreshing(true)")
                threading.Thread(target=self._do_sat_auto_refresh, daemon=True).start()
            else:
                rem = max(0, (self._sat_next_refresh - now).total_seconds())
                m, s = int(rem // 60), int(rem % 60)
                self._eval_js(f"window.updateCountdown('sat', '{m:02d}:{s:02d}')")
        except Exception as e:
            logger.error(f"sat countdown error: {e}")
        self._sat_timer = threading.Timer(1.0, self._sat_countdown_loop)
        self._sat_timer.daemon = True
        self._sat_timer.start()

    def _do_sat_auto_refresh(self):
        sat = self.satellite_id
        color = self.satellite_color
        size = self.satellite_size
        try:
            path = fetch_satellite_image(satellite=sat, color=color, target_size=size)
        except Exception as e:
            logger.error(f"sat auto refresh error: {e}")
            return
        if not path:
            return
        self.sat_image_path = path
        now = datetime.now()
        name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
        b64 = self._image_to_b64(path)
        self._eval_js("window.onSatRefreshing(false)")
        self._eval_js("window.onSatRefreshed(" + json.dumps({"image": b64, "title": name}) + ")")
        if self.current_source == "satellite":
            style = self.config.get("wallpaper_style", "fill")
            wp = watermark_image(path, left_text=f"来源: {name}",
                                 right_text=f"拍摄时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
                                 output_key=f"sat_{sat}")
            set_wallpaper(wp, f"sat_{sat}", style=style)
            self._eval_js("window.onStatus('🛰 自动刷新 | " +
                          now.strftime("%H:%M") + " | 壁纸同步更新', true)")

    def toggle_sdo_auto_refresh(self):
        self.sdo_auto_refresh = not self.sdo_auto_refresh
        self.config["sdo_auto_refresh"] = self.sdo_auto_refresh
        save_config(self.config)
        if self.sdo_auto_refresh:
            self._start_sdo_timer()
        else:
            self._stop_sdo_timer()
        return {"on": self.sdo_auto_refresh}

    def _start_sdo_timer(self):
        self._stop_sdo_timer()
        self._sdo_next_refresh = datetime.now() + timedelta(minutes=SDO_REFRESH_MIN)
        self._sdo_timer = threading.Timer(1.0, self._sdo_countdown_loop)
        self._sdo_timer.daemon = True
        self._sdo_timer.start()

    def _stop_sdo_timer(self):
        if self._sdo_timer:
            self._sdo_timer.cancel()
            self._sdo_timer = None
        self._sdo_next_refresh = None

    def _sdo_countdown_loop(self):
        if not self.sdo_auto_refresh or not self._window:
            return
        try:
            now = datetime.now()
            if self._sdo_next_refresh and (self._sdo_next_refresh - now).total_seconds() <= 0:
                self._sdo_next_refresh = now + timedelta(minutes=SDO_REFRESH_MIN)
                self._eval_js("window.onSdoRefreshing(true)")
                threading.Thread(target=self._do_sdo_auto_refresh, daemon=True).start()
            else:
                rem = max(0, (self._sdo_next_refresh - now).total_seconds())
                m, s = int(rem // 60), int(rem % 60)
                self._eval_js(f"window.updateCountdown('sdo', '{m:02d}:{s:02d}')")
        except Exception as e:
            logger.error(f"sdo countdown error: {e}")
        self._sdo_timer = threading.Timer(1.0, self._sdo_countdown_loop)
        self._sdo_timer.daemon = True
        self._sdo_timer.start()

    def _do_sdo_auto_refresh(self):
        band = self.sdo_band
        try:
            path = fetch_sdo_image(band=band)
        except Exception as e:
            logger.error(f"sdo auto refresh error: {e}")
            return
        if not path:
            return
        self.sdo_image_path = path
        now = datetime.now()
        name = SDO_BANDS.get(band, {}).get("name", band)
        b64 = self._image_to_b64(path)
        self._eval_js("window.onSdoRefreshing(false)")
        self._eval_js("window.onSdoRefreshed(" + json.dumps({"image": b64, "title": name}) + ")")
        if self.current_source == "sdo":
            style = self.config.get("wallpaper_style", "fill")
            wp = watermark_image(path, left_text="来源: NASA SDO 太阳观测",
                                 right_text=f"波段: {name} | {now.strftime('%Y-%m-%d %H:%M')}",
                                 output_key=f"sdo_{band}")
            set_wallpaper(wp, f"sdo_{band}", style=style)
            self._eval_js("window.onStatus('☀ SDO 自动刷新 | " +
                          now.strftime("%H:%M") + " | 壁纸同步更新', true)")

    # ------------------------------------------------------------------
    # 设置 / 状态
    # ------------------------------------------------------------------
    def get_settings(self):
        return {
            "api_key": self.config.get("api_key", DEFAULT_API_KEY),
            "wallpaper_style": self.config.get("wallpaper_style", "fill"),
            "auto_update": self.config.get("auto_update", True),
            "hd": self.config.get("hd", True),
            "auto_start": self.config.get("auto_start", False),
        }

    def save_settings(self, s):
        self.config["api_key"] = (s.get("api_key") or DEFAULT_API_KEY).strip() or DEFAULT_API_KEY
        self.config["wallpaper_style"] = s.get("wallpaper_style", "fill")
        self.config["auto_update"] = bool(s.get("auto_update", True))
        self.config["hd"] = bool(s.get("hd", True))
        self.config["auto_start"] = bool(s.get("auto_start", False))
        save_config(self.config)
        return {"ok": True}

    def get_status(self):
        n = len(self.metadata.get("images", {}))
        disk = 0
        try:
            for f in IMAGE_CACHE_DIR.glob("*"):
                if f.is_file():
                    disk += f.stat().st_size
        except Exception:
            pass
        return {
            "cache_count": n,
            "disk_mb": round(disk / (1024 * 1024), 1),
            "scheduler_running": is_scheduler_running(),
            "status_text": self._status_text(self.current_source),
        }

    def _status_text(self, source):
        if source == "apod":
            return "天文图片模式 · NASA APOD 每日精选"
        if source == "satellite":
            name = GEOSTATIONARY_SATELLITES.get(self.satellite_id, {}).get("name", "卫星")
            color_label = "自然色" if self.satellite_color == "natural_color" else "地球色"
            return f"卫星影像模式 · {name} {color_label}"
        if source == "sdo":
            name = SDO_BANDS.get(self.sdo_band, {}).get("name", "太阳")
            return f"太阳观测模式 · NASA SDO {name}"
        return ""

    # ------------------------------------------------------------------
    # 首次自动获取
    # ------------------------------------------------------------------
    def auto_fetch_on_startup(self):
        if self.metadata.get("images"):
            return {"ok": True, "already": True}
        threading.Thread(target=self._do_auto_fetch, daemon=True).start()
        return {"ok": True, "started": True}

    def _do_auto_fetch(self):
        try:
            end = datetime.now()
            start = end - timedelta(days=10)
            api_key = self.config.get("api_key") or DEFAULT_API_KEY
            images = fetch_apod_range(start_date=start.strftime("%Y-%m-%d"),
                                      end_date=end.strftime("%Y-%m-%d"), api_key=api_key)
            if images:
                meta = load_metadata()
                for img in images:
                    meta["images"][img.date] = img.to_dict()
                save_metadata(meta)
                self.metadata = meta
                self.rebuild_category_data()
                self._eval_js("window.onAutoFetchDone(" + str(len(images)) + ")")
            else:
                self._eval_js("window.onAutoFetchFail()")
        except Exception as e:
            logger.error(f"auto fetch error: {e}")
            self._eval_js("window.onAutoFetchFail()")

    # ------------------------------------------------------------------
    # 窗口控制
    # ------------------------------------------------------------------
    def minimize(self):
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass
        return {"ok": True}

    def quit_app(self):
        self._stop_sat_timer()
        self._stop_sdo_timer()
        try:
            stop_scheduler()
        except Exception:
            pass
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
        # 确保进程真正退出（pywebview window.destroy 不一定能让 start() 返回）
        import os, threading
        threading.Thread(target=lambda: os._exit(0), daemon=True).start()
        return {"ok": True}

    def get_initial_state(self):
        return {
            "source": self.current_source,
            "categories": self.get_categories(),
            "apod": self.get_current_apod(),
            "satellites": self.get_satellites(),
            "sat_info": self.get_sat_info(),
            "sdo_bands": self.get_sdo_bands(),
            "sdo_info": self.get_sdo_band_info(),
            "settings": self.get_settings(),
            "status": self.get_status(),
            "sat_auto": self.sat_auto_refresh,
            "sdo_auto": self.sdo_auto_refresh,
            "accents": ACCENTS,
            "version": APP_VERSION,
        }
