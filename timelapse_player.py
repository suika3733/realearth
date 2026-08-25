"""时间流逝 - 动态壁纸播放器

三层结构：
- FrameSource      帧列表维护（全量扫描目录，新帧自动出现在队列尾部 = 实时模式）
- Win32Backend     渲染后端：UpdateLayeredWindow 分层置底窗口（纯 ctypes, 无 WebView2 依赖）
- TimelapsePlayer  播放内核：fps 定时循环、低帧数降速、暂停/恢复、帧缓存

实现说明（为什么不用 pywebview 第二窗口）：
pywebview 多窗口 transparent 在 EdgeChromium 下有已知兼容问题，且每个窗口是独立
WebView2 进程（内存开销大）。Win32 分层窗口直接 GDI 渲染，可控性、性能都更优。

桌面层级实现（Wallpaper Engine 同款机制）：
桌面由 explorer 的 Progman 根窗口管理，其下有壁纸层 WorkerW 和图标层 WorkerW
（后者含 SHELLDLL_DefView -> SysListView32 图标列表）。仅把壁纸窗口置底为顶层
窗口会盖住桌面图标（z-order 中 Progman 比普通顶层窗口更底）。正确做法是向
Progman 发送 0x052C 触发 WorkerW 分离，再把壁纸窗口 SetParent 到"壁纸层 WorkerW"
（不含图标的那个）之下，使其嵌入图标层之下，桌面图标始终显示在壁纸之上。
explorer 重启会重建 WorkerW，因此每次 keep_bottom 周期检测父窗口丢失则重新挂接。
"""
import ctypes
import datetime
import logging
import threading
import time
from collections import OrderedDict
from ctypes import wintypes

from PIL import Image

logger = logging.getLogger(__name__)

# ---------------- Win32 常量 ----------------
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
HWND_BOTTOM = 1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
WM_ERASEBKGND = 0x0014
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SMTO_NORMAL = 0x0000
GA_PARENT = 1
# Progman 触发 WorkerW 分离的系统消息（explorer 内部，Wallpaper Engine 同款）
WM_SPAWN_WORKERW = 0x052C

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# Python 3.13 的 ctypes.wintypes 已移除 LRESULT；且 64 位 Windows 上 WPARAM/LPARAM
# 必须是 64 位（wintypes 中仍为 32 位 c_long），否则消息回调参数溢出。
if ctypes.sizeof(ctypes.c_void_p) == 8:
    WPARAM = ctypes.c_uint64
    LPARAM = ctypes.c_int64
    LRESULT = ctypes.c_ssize_t
else:
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM
    LRESULT = ctypes.c_long

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND,
                             wintypes.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, LPARAM)


class WNDCLASSW(ctypes.Structure):
    """Python 3.13 的 ctypes.wintypes 已移除 WNDCLASSW，这里自行定义"""
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


@WNDPROC
def _wnd_proc(hwnd, msg, wp, lp):
    if msg == WM_ERASEBKGND:
        return 1
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 1)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte),
                ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


# ---------------- 64 位句柄安全：显式声明参数/返回类型 ----------------
# 不设置时 ctypes 默认按 32 位 c_int 处理返回值，64 位系统上句柄值可能被截断
# （本机句柄通常恰好符号扩展无碍，但显式声明可消除所有环境下的隐患）
HDC = wintypes.HDC
HGDIOBJ = wintypes.HGDIOBJ

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wintypes.DWORD

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
user32.ReleaseDC.restype = ctypes.c_int

gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.DeleteDC.argtypes = [HDC]
gdi32.DeleteDC.restype = ctypes.c_int

gdi32.CreateDIBSection.argtypes = [HDC, ctypes.POINTER(BITMAPINFO),
                                   wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HANDLE  # HBITMAP

gdi32.SelectObject.argtypes = [HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE      # HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL

user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

# ---------------- 桌面挂接（嵌入 WorkerW，图标层之下） ----------------
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.EnumWindows.argtypes = [WNDENUMPROC, LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, WPARAM, LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_uint64)]
user32.SendMessageTimeoutW.restype = LRESULT


class FrameSource:
    """帧列表维护：目录全量扫描，新帧自动入队（实时语义）"""

    def __init__(self, satellite: str, date: str):
        from archive import _day_dir
        self.dir = _day_dir(satellite, date)

    def list(self) -> list:
        if not self.dir.exists():
            return []
        return sorted(p.name for p in self.dir.glob("*.jpg"))


def _find_wallpaper_target():
    """定位壁纸窗口应挂接的桌面宿主，返回 (host, insert_after)。

    host        壁纸窗口的新父窗口
    insert_after z-order 插入点（SetWindowPos 的 hWndInsertAfter）

    策略（Wallpaper Engine / Lively 同款）：
    1. 向 Progman 发 0x052C 触发 WorkerW 分离。注意：分离可能不是一次到位
       （defview 常需要多次触发才从 Progman/壁纸层迁到独立的图标层 WorkerW），
       因此循环发送 4 次并等待，直到枚举出"不含 SHELLDLL_DefView 的空 WorkerW"。
    2. 宿主选择优先级：
       a. Progman 的 owner/子窗口中的空 WorkerW（经典 Win10 布局）
       b. z-order 中位于图标层 WorkerW 之后的空 WorkerW（Win11 独立顶层布局）
       c. 任意空 WorkerW
       d. 最后手段：挂到 Progman、插到图标层 WorkerW 之后（图标不被遮挡，
          但部分系统上壁纸可能被 defview 背景盖住）
    找不到 Progman 返回 (None, None)（保持原顶层置底行为）。
    """
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None, None
    # 循环触发分离：explorer 可能需多次 0x052C 才把 defview 迁入图标层 WorkerW
    for _ in range(4):
        result = ctypes.c_uint64()
        user32.SendMessageTimeoutW(progman, WM_SPAWN_WORKERW, 0, 0,
                                   SMTO_NORMAL, 500, ctypes.byref(result))
        time.sleep(0.15)
    empty_workers = []      # 不含 defview 的 WorkerW（壁纸层候选）
    defview_workers = []    # 含 defview 的 WorkerW（图标层）
    progman_workers = []    # Progman owner/子窗口中的 WorkerW（经典布局）

    @WNDENUMPROC
    def _cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(64)
        n = user32.GetClassNameW(hwnd, buf, len(buf))
        if n and buf.value == "WorkerW":
            if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
                defview_workers.append(hwnd)          # 图标层 WorkerW
            else:
                empty_workers.append(hwnd)            # 壁纸层 WorkerW（不含图标）
                if user32.GetParent(hwnd) == progman:
                    progman_workers.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    # a) 经典 Win10：Progman 相关（owner/子窗口）的空 WorkerW
    if progman_workers:
        return progman_workers[0], HWND_BOTTOM
    # b) 图标层存在：取 z-order 中位于图标层之后的空 WorkerW
    if defview_workers:
        cur = defview_workers[0]
        while cur:
            cur = user32.FindWindowExW(None, cur, "WorkerW", None)
            if not cur:
                break
            if cur not in defview_workers:
                return cur, HWND_BOTTOM
        # d) 回退：挂到 Progman，插到图标层 WorkerW 之后
        return progman, defview_workers[0]
    # c) 任意空 WorkerW（无图标层可参考时）
    if empty_workers:
        return empty_workers[0], HWND_BOTTOM
    return progman, HWND_BOTTOM


class Win32Backend:
    """UpdateLayeredWindow 分层窗口（置底壁纸窗口）"""

    def __init__(self, size=None):
        self.size = size  # (w, h) 物理像素；None = 当前屏幕
        self.hwnd = None
        self._host = None  # 桌面宿主（壁纸层 WorkerW 或 Progman）
        self._w = 0
        self._h = 0
        self._screen_dc = None
        self._mem_dc = None

    def create(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        self._cleanup_stale_windows()
        if self.size:
            self._w, self._h = int(self.size[0]), int(self.size[1])
        else:
            self._w = user32.GetSystemMetrics(SM_CXSCREEN)
            self._h = user32.GetSystemMetrics(SM_CYSCREEN)
        cls = "RealEarthTimelapse"
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = _wnd_proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        # 分层壁纸窗口 WS_EX_NOACTIVATE 不参与鼠标交互，无需光标
        wc.hCursor = None
        user32.RegisterClassW(ctypes.byref(wc))  # 重复注册会失败，忽略
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            cls, "RealEarthTimelapse", WS_POPUP | WS_VISIBLE,
            0, 0, self._w, self._h, None, None, hinst, None)
        if not self.hwnd:
            raise RuntimeError(f"CreateWindowExW failed: {kernel32.GetLastError()}")
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        self.set_bottom()
        logger.info(f"timelapse window created {self._w}x{self._h}")
        return True

    def _cleanup_stale_windows(self):
        """清理本进程残留的 RealEarthTimelapse 壁纸窗口。

        场景：本进程异常退出前创建的壁纸窗口因 explorer 重启脱离 WorkerW
        挂在桌面，或重复实例导致多窗口叠加。DestroyWindow 只能销毁同进程
        窗口（跨进程返回 ACCESS_DENIED），因此：
        - 本进程窗口 → 直接销毁
        - 其他进程窗口（旧版 exe 仍在运行）→ 记警告，提示用户关闭旧进程
        """
        my_pid = kernel32.GetCurrentProcessId()
        doomed = []
        wbuf = ctypes.create_unicode_buffer(64)

        @WNDENUMPROC
        def _cb(hwnd, lparam):
            n = user32.GetClassNameW(hwnd, wbuf, len(wbuf))
            if n and wbuf.value == "RealEarthTimelapse":
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == my_pid:
                    doomed.append(hwnd)
                else:
                    logger.warning(
                        f"timelapse window 0x{hwnd:X} belongs to another "
                        f"process (pid={pid.value}), skip destroy; "
                        "close the old instance to avoid overlay")
            # 递归子窗口：SetParent 到 WorkerW 下的窗口可能不是顶层窗口
            user32.EnumChildWindows(hwnd, _cb, 0)
            return True

        user32.EnumWindows(_cb, 0)
        for h in doomed:
            if h != self.hwnd:
                user32.DestroyWindow(h)
                logger.info(f"timelapse cleaned stale window 0x{h:X}")

    def show(self, rgba_image: Image.Image):
        """更新一帧到分层窗口（PIL RGBA, 已缩放到屏幕尺寸）"""
        w, h = rgba_image.size
        bgra = rgba_image.tobytes("raw", "BGRA")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi), 0,
                                      ctypes.byref(bits), None, 0)
        if not hbmp or not bits.value:
            logger.error("timelapse CreateDIBSection failed "
                         f"(hbmp={hbmp!r} bits={bits.value!r})")
            return
        ctypes.memmove(bits, bgra, len(bgra))
        old = gdi32.SelectObject(self._mem_dc, hbmp)
        pt_dst = wintypes.POINT(0, 0)
        sz = wintypes.SIZE(w, h)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(self.hwnd, self._screen_dc,
                                        ctypes.byref(pt_dst), ctypes.byref(sz),
                                        self._mem_dc, ctypes.byref(pt_src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            logger.error("timelapse UpdateLayeredWindow failed: "
                         f"{kernel32.GetLastError()}")
        gdi32.SelectObject(self._mem_dc, old)
        gdi32.DeleteObject(hbmp)

    def set_bottom(self):
        """置底 + 确保挂接在桌面宿主（图标层之下）。

        每次 keep_bottom 周期都会重新定位宿主：explorer 重启会销毁重建
        WorkerW，原父窗口失效，此时自动重新 SetParent，保证壁纸一直嵌在
        图标层之下。注意 GetParent 对跨进程父（explorer 的窗口）返回 0，
        须用 GetAncestor 判断当前父窗口。
        """
        host, insert_after = _find_wallpaper_target()
        if host:
            cur = user32.GetAncestor(self.hwnd, GA_PARENT)
            if cur != host:
                user32.SetParent(self.hwnd, host)
                self._host = host
                # 重挂后强制显示：部分系统 SetParent 后窗口会停在隐藏态
                user32.ShowWindow(self.hwnd, 5)  # SW_SHOW
                logger.info(f"timelapse re-attached to desktop host "
                            f"0x{host:X} (prev 0x{cur or 0:X})")
        else:
            insert_after = HWND_BOTTOM
        user32.SetWindowPos(self.hwnd, insert_after, 0, 0, self._w, self._h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def attach_to_desktop(self):
        """（显式）挂接到桌面图标层之下，返回是否成功。create() 已自动调用，
        单独暴露供诊断/重挂使用。"""
        host, insert_after = _find_wallpaper_target()
        if not host:
            logger.warning("timelapse desktop host not found, "
                           "fallback to plain bottom")
            return False
        user32.SetParent(self.hwnd, host)
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW：重挂后强制可见
        user32.SetWindowPos(self.hwnd, insert_after, 0, 0, self._w, self._h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self._host = host
        logger.info(f"timelapse attached to desktop host 0x{host:X} "
                    f"insert_after={insert_after!r}")
        return True

    def close(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        if self._screen_dc:
            user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None


class TimelapsePlayer:
    """播放内核：fps 定时循环、循环播放、低帧数降速、暂停/恢复"""

    def __init__(self, fps=10, low_fps=3, low_threshold=8,
                 keep_bottom_interval=5.0, frame_cache_size=10):
        self.fps = int(fps)
        self.low_fps = int(low_fps)
        self.low_threshold = low_threshold
        self.keep_bottom_interval = keep_bottom_interval
        self._source = None
        self._backend = None
        self._thread = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._cache = OrderedDict()
        self._cache_size = frame_cache_size
        self._lock = threading.Lock()
        self._state = {"running": False, "sat": None, "date": None,
                       "frames": 0, "fps": self.fps, "paused": False}

    def start(self, satellite: str, date: str = None, fps: int = None) -> dict:
        if self._thread and self._thread.is_alive():
            self.stop()
        if fps:
            self.fps = int(fps)
        date = date or datetime.date.today().isoformat()
        self._source = FrameSource(satellite, date)
        self._backend = Win32Backend()
        self._stop.clear()
        self._pause.set()
        with self._lock:
            self._state.update(sat=satellite, date=date, paused=False,
                               fps=self.fps)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # 等待窗口创建成功（最多 5s）
        for _ in range(50):
            if self._backend.hwnd:
                break
            if not self._thread.is_alive():
                break
            time.sleep(0.1)
        return self.state()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        # 窗口由播放线程在 _loop finally 中销毁（DestroyWindow 须由窗口所属线程调用，
        # 跨线程销毁会失败/死锁），这里只断开引用
        self._backend = None
        self._cache.clear()
        with self._lock:
            self._state.update(running=False, sat=None, date=None,
                               frames=0, paused=False)

    def pause(self):
        self._pause.clear()
        with self._lock:
            self._state["paused"] = True

    def resume(self):
        self._pause.set()
        with self._lock:
            self._state["paused"] = False

    def state(self) -> dict:
        with self._lock:
            s = dict(self._state)
        s["running"] = bool(self._thread and self._thread.is_alive())
        if self._source:
            s["frames"] = len(self._source.list())
        return s

    # ---------------- 内部 ----------------
    def _load_frame(self, name: str) -> Image.Image:
        """加载 + 缩放 + RGBA 转换，带 LRU 缓存（避免每帧重复解码）"""
        if name in self._cache:
            self._cache.move_to_end(name)
            return self._cache[name]
        img = Image.open(self._source.dir / name)
        img = img.convert("RGBA")
        sw, sh = self._backend._w, self._backend._h
        if img.size != (sw, sh):
            img = img.resize((sw, sh), Image.LANCZOS)
        self._cache[name] = img
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return img

    def _loop(self):
        try:
            self._backend.create()
        except Exception as e:
            logger.error(f"timelapse backend create failed: {e}")
            with self._lock:
                self._state["running"] = False
                self._state["error"] = str(e)
            return
        with self._lock:
            self._state["running"] = True
        idx = 0
        last_bottom = 0.0
        try:
            while not self._stop.is_set():
                self._pause.wait()          # 暂停时阻塞
                if self._stop.is_set():
                    break
                frames = self._source.list()
                n = len(frames)
                fps = self.fps if n >= self.low_threshold else self.low_fps
                if n:
                    name = frames[idx % n]
                    idx += 1
                    try:
                        self._backend.show(self._load_frame(name))
                    except Exception as e:
                        logger.warning(f"show frame error: {e}")
                else:
                    fps = 1.0   # 暂无帧，低频空转
                now = time.time()
                if now - last_bottom > self.keep_bottom_interval:
                    try:
                        self._backend.set_bottom()
                    except Exception:
                        pass
                    last_bottom = now
                with self._lock:
                    self._state["frames"] = n
                    self._state["fps"] = fps
                self._stop.wait(1.0 / fps)
        finally:
            with self._lock:
                self._state["running"] = False
            try:
                self._backend.close()
            except Exception:
                pass
