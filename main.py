"""RealEarth — Web 视图桌面应用 (pywebview + HTML/CSS)

UI 严格按 "Cosmic Observatory" v5.0 设计系统 (见 RealEarth_Design_Spec.md)。
后端逻辑见 backend.py, 与 UI 框架完全解耦。
"""
import os
import sys

import webview

from backend import RealEarthBackend


def resource_path(rel):
    """定位资源文件: 开发时相对脚本, 打包后位于 sys._MEIPASS"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class Api:
    """暴露给前端 JS (window.pywebview.api.*) 的桥接层"""

    def __init__(self):
        self._backend = RealEarthBackend()
        self._window_ref = None  # 下划线前缀，避免 pywebview 递归暴露
        self._maximized = False

    # ---- 生命周期 ----
    def init(self):
        """前端 DOM 就绪后调用: 恢复自动刷新 + 返回初始状态"""
        self._backend.resume_auto_refresh()
        return self._backend.get_initial_state()

    def get_initial_state(self):
        return self._backend.get_initial_state()

    # ---- 面板/APOD ----
    def set_source(self, source):
        return self._backend.set_source(source)

    def select_category(self, key):
        return self._backend.select_category(key)

    def prev_image(self):
        return self._backend.prev_image()

    def next_image(self):
        return self._backend.next_image()

    def set_apod_wallpaper(self):
        return self._backend.set_apod_wallpaper()

    def fetch_apod(self, days):
        return self._backend.fetch_apod(days)

    def update_now(self):
        return self._backend.update_now()

    # ---- 卫星 ----
    def get_satellites(self):
        return self._backend.get_satellites()

    def set_satellite(self, sat_id):
        return self._backend.set_satellite(sat_id)

    def set_sat_color(self, color):
        self._backend.set_sat_color(color)

    def set_sat_size(self, size):
        self._backend.set_sat_size(size)

    def fetch_satellite(self):
        return self._backend.fetch_satellite()

    def set_sat_wallpaper(self):
        return self._backend.set_sat_wallpaper()

    # ---- SDO ----
    def get_sdo_bands(self):
        return self._backend.get_sdo_bands()

    def set_sdo_band(self, band):
        return self._backend.set_sdo_band(band)

    def fetch_sdo(self):
        return self._backend.fetch_sdo()

    def set_sdo_wallpaper(self):
        return self._backend.set_sdo_wallpaper()

    # ---- 自动刷新 ----
    def toggle_sat_auto_refresh(self):
        return self._backend.toggle_sat_auto_refresh()

    def toggle_sdo_auto_refresh(self):
        return self._backend.toggle_sdo_auto_refresh()

    # ---- 设置 ----
    def get_settings(self):
        return self._backend.get_settings()

    def save_settings(self, s):
        return self._backend.save_settings(s)

    def get_status(self):
        return self._backend.get_status()

    # ---- 窗口控制 ----
    def minimize(self):
        if self._window_ref:
            try:
                self._window_ref.minimize()
            except Exception:
                pass
        return {"ok": True}

    def toggle_maximize(self):
        if self._window_ref:
            try:
                if self._maximized:
                    self._window_ref.restore()
                else:
                    self._window_ref.maximize()
                self._maximized = not self._maximized
            except Exception:
                pass
        return {"ok": True}

    def quit_app(self):
        return self._backend.quit_app()


def main():
    api = Api()
    window = webview.create_window(
        "RealEarth · 真实地球壁纸",
        url=resource_path(os.path.join("web", "index.html")),
        js_api=api,
        frameless=True,
        width=1200,
        height=780,
        min_size=(960, 600),
        text_select=False,
        background_color="#080B14",
    )
    api._window_ref = window      # 下划线前缀，避免 pywebview 递归暴露 window 对象
    api._backend._window = window  # 同上
    # 首次启动自动拉取近 10 天 APOD
    api._backend.auto_fetch_on_startup()
    webview.start(debug=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 兜底: 异常写入日志, 避免窗口模式静默退出难以排查
        import traceback
        try:
            if getattr(sys, "frozen", False):
                base_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(base_dir, "error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("=" * 40 + "\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
