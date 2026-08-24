"""时间流逝导出 - 从存档帧序列合成动画 (GIF / MP4)

- GIF: Pillow save_all，零新增依赖
- MP4: imageio + imageio-ffmpeg（可选依赖，打包时引入）；未安装时返回明确错误
- interval 抽帧间隔：1=全部，2=隔一取一，用于控制导出体积
- 进度经 TaskManager.report 上报，支持取消
"""
import datetime
import logging

from PIL import Image

from config import EXPORT_DIR
from archive import list_frames
from tasks import TaskManager

logger = logging.getLogger(__name__)


def _collect_frames(satellite: str, start: str, end: str, interval: int) -> list:
    """按日期段收集帧路径（升序），interval 抽帧"""
    frames = []
    day = datetime.date.fromisoformat(start)
    endd = datetime.date.fromisoformat(end)
    while day <= endd:
        ds = day.strftime("%Y-%m-%d")
        frames.extend(f["path"] for f in list_frames(satellite, ds))
        day += datetime.timedelta(days=1)
    frames.sort()
    if interval and interval > 1:
        frames = frames[::interval]
    return frames


def export_timelapse(satellite, start, end, fmt="gif", fps=10,
                     interval=1, task=None) -> str:
    """从存档合成动画，输出到 EXPORT_DIR，返回输出路径"""
    frames = _collect_frames(satellite, start, end, int(interval or 1))
    total = len(frames)
    if not total:
        raise RuntimeError("该日期段没有可导出的帧")
    if total > 2000:
        raise RuntimeError(f"帧数过多（{total}），请缩短日期段或增大抽帧间隔")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{satellite}_{start}_{end}_fps{fps}.{fmt}"
    out = EXPORT_DIR / name

    if fmt == "gif":
        _export_gif(frames, out, fps, total, task)
    elif fmt == "mp4":
        _export_mp4(frames, out, fps, total, task)
    else:
        raise ValueError(f"不支持的格式: {fmt}")

    TaskManager.report(task, current=total, total=total, msg=f"完成: {out.name}")
    return str(out)


def _export_gif(frames, out, fps, total, task):
    duration = int(1000 / max(1, fps))
    imgs = []
    try:
        for i, p in enumerate(frames):
            if TaskManager.is_canceled(task):
                raise RuntimeError("已取消")
            im = Image.open(p)
            if im.mode != "RGB":
                im = im.convert("RGB")
            imgs.append(im)
            TaskManager.report(task, current=i + 1, total=total,
                               msg=f"读取帧 {i + 1}/{total}")
        imgs[0].save(out, save_all=True, append_images=imgs[1:],
                     duration=duration, loop=0, optimize=False)
    finally:
        for im in imgs:
            im.close()


def _export_mp4(frames, out, fps, total, task):
    try:
        import imageio.v2 as imageio
    except ImportError:
        try:
            import imageio_ffmpeg  # noqa: F401  确保 ffmpeg 二进制可用
            import imageio.v2 as imageio
        except ImportError:
            raise RuntimeError("MP4 导出需要 imageio-ffmpeg，请改用 GIF 导出")
    writer = imageio.get_writer(str(out), fps=max(1, int(fps)),
                                codec="libx264", quality=8)
    try:
        for i, p in enumerate(frames):
            if TaskManager.is_canceled(task):
                writer.close()
                raise RuntimeError("已取消")
            im = Image.open(p).convert("RGB")
            writer.append_data(im)
            im.close()
            TaskManager.report(task, current=i + 1, total=total,
                               msg=f"编码 {i + 1}/{total}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
