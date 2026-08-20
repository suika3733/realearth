"""Windows 壁纸设置 - 支持壁纸样式控制和图片水印"""
import ctypes
import logging
import shutil
import winreg
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 水印缓存目录
_WATERMARK_DIR = Path.home() / ".nasa_wallpaper" / "watermarked"
_WATERMARK_DIR.mkdir(parents=True, exist_ok=True)

# 水印字体查找（按优先级）
_WATERMARK_FONTS = [
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/arial.ttf",    # Arial
    "Microsoft YaHei",
    "SimHei",
    "Arial",
]


def _get_watermark_font(size: int) -> ImageFont.FreeTypeFont:
    """获取可用的中文字体"""
    for name in _WATERMARK_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def watermark_image(
    image_path: str,
    left_text: str,
    right_text: str | None = None,
    output_key: str | None = None,
) -> str:
    """在图片右下角空白区域添加半透明水印标注（角标风格），保存到缓存目录

    Args:
        image_path: 原始图片路径
        left_text: 第一行文字（来源信息，如 "NASA 每日天文图片"）
        right_text: 第二行文字（时间信息，如 "2026-08-11 | M31 仙女座星系"）
        output_key: 输出文件名键（不含扩展名）

    Returns:
        带水印的图片路径
    """
    output_path = _WATERMARK_DIR / f"{output_key or Path(image_path).stem}.jpg"
    if output_path.exists():
        output_path.unlink()

    try:
        img = Image.open(image_path).convert("RGBA")
        iw, ih = img.size

        # 字体大小：基于图片尺寸自适应
        base_size = max(14, min(28, int(min(iw, ih) * 0.022)))
        font_main = _get_watermark_font(base_size)
        font_sub = _get_watermark_font(max(10, base_size - 4))

        # 文字尺寸测量
        draw_tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l_bbox = draw_tmp.textbbox((0, 0), left_text, font=font_main)
        lw = l_bbox[2] - l_bbox[0]
        lh = l_bbox[3] - l_bbox[1]

        text_w = lw
        text_h = lh
        has_sub = False
        if right_text:
            has_sub = True
            r_bbox = draw_tmp.textbbox((0, 0), right_text, font=font_sub)
            rw = r_bbox[2] - r_bbox[0]
            rh = r_bbox[3] - r_bbox[1]
            text_w = max(lw, rw)
            text_h = lh + 2 + rh

        # 角标尺寸和位置：放在右上角空白区域（避免被 Windows 任务栏遮挡）
        padding_x = int(iw * 0.025)
        padding_y = int(ih * 0.015)
        badge_w = text_w + padding_x * 2
        badge_h = text_h + padding_y * 2
        badge_x = iw - badge_w - padding_x  # 靠右
        badge_y = padding_y  # 靠顶（任务栏通常在底部）

        # 创建半透明覆盖层
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 圆角矩形背景：半透明深色
        radius = 10
        bg_alpha = 140
        # 用多边形画圆角背景
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=radius,
            fill=(8, 12, 22, bg_alpha),
        )

        # 细边框
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=radius,
            outline=(100, 160, 220, 80),
            width=1,
        )

        # 第一行文字（来源）
        text_x = badge_x + padding_x
        text_y = badge_y + padding_y
        draw.text(
            (text_x, text_y),
            left_text,
            fill=(220, 230, 250, 230),
            font=font_main,
        )

        # 第二行文字（时间/信息）
        if has_sub:
            text_y2 = text_y + lh + 2
            draw.text(
                (text_x, text_y2),
                right_text,
                fill=(140, 165, 200, 200),
                font=font_sub,
            )

        # 合成并保存
        result = Image.alpha_composite(img, overlay).convert("RGB")
        result.save(str(output_path), "JPEG", quality=92)

        logger.info(f"Watermarked: {output_path}")
        return str(output_path)
    except Exception as e:
        logger.error(f"Watermark failed: {e}")
        return image_path  # 降级：返回原图

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

# 合成壁纸缓存目录
_COMPOSED_DIR = Path.home() / ".nasa_wallpaper" / "composed"
_COMPOSED_DIR.mkdir(parents=True, exist_ok=True)


def _get_screen_size() -> tuple[int, int]:
    """获取主屏幕分辨率（宽, 高）"""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception as e:
        logger.warning(f"Get screen size failed: {e}")
        return 1920, 1080


def _is_custom_position(pos_x: int, pos_y: int, scale: int) -> bool:
    """判断是否启用了自定义位置/缩放（非默认居中+原尺寸）"""
    return not (scale == 50 and pos_x == 50 and pos_y == 50)


def compose_wallpaper(image_path: str, pos_x: int = 50, pos_y: int = 50,
                      scale: int = 50) -> str | None:
    """根据位置和缩放参数将图片合成到屏幕尺寸的黑色画布上

    Args:
        image_path: 原始图片路径（已加水印）
        pos_x: 水平位置百分比 0-100（50=居中）
        pos_y: 垂直位置百分比 0-100（50=居中）
        scale: 缩放百分比 0-100（50=原始大小，0=极小，100=放大2倍）

    Returns:
        合成后的图片路径；若无需合成（默认参数）返回 None
    """
    if not _is_custom_position(pos_x, pos_y, scale):
        return None

    try:
        img = Image.open(image_path).convert("RGB")
        sw, sh = _get_screen_size()

        # 缩放原图（50% = 原始大小）
        orig_w, orig_h = img.size
        factor = scale / 50.0
        new_w = max(1, int(orig_w * factor))
        new_h = max(1, int(orig_h * factor))
        if factor != 1.0:
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # 创建黑色画布
        canvas = Image.new("RGB", (sw, sh), (0, 0, 0))

        # 计算贴图位置：按百分比在可用空间内定位
        paste_x = int((sw - new_w) * pos_x / 100)
        paste_y = int((sh - new_h) * pos_y / 100)

        # 图片可能大于画布，超出部分裁剪
        src_x0 = max(0, -paste_x)
        src_y0 = max(0, -paste_y)
        dst_x0 = max(0, paste_x)
        dst_y0 = max(0, paste_y)
        copy_w = min(new_w - src_x0, sw - dst_x0)
        copy_h = min(new_h - src_y0, sh - dst_y0)

        if copy_w > 0 and copy_h > 0:
            crop = img.crop((src_x0, src_y0, src_x0 + copy_w, src_y0 + copy_h))
            canvas.paste(crop, (dst_x0, dst_y0))

        output = _COMPOSED_DIR / f"composed_{Path(image_path).stem}.jpg"
        canvas.save(str(output), "JPEG", quality=92)
        logger.info(f"Composed wallpaper: {output} (pos={pos_x},{pos_y} scale={scale})")
        return str(output)
    except Exception as e:
        logger.error(f"Compose wallpaper failed: {e}")
        return None

# 壁纸样式注册表值映射
STYLE_REGISTRY = {
    "center":  {"WallpaperStyle": "0", "TileWallpaper": "0"},
    "tile":    {"WallpaperStyle": "0", "TileWallpaper": "1"},
    "stretch": {"WallpaperStyle": "2", "TileWallpaper": "0"},
    "fit":     {"WallpaperStyle": "6", "TileWallpaper": "0"},
    "fill":    {"WallpaperStyle": "10", "TileWallpaper": "0"},
}


def set_wallpaper_style(style: str = "fill"):
    """通过注册表设置壁纸样式

    参考 LiveEarth 项目实现，与 set_wallpaper 配合使用。
    样式值：
    - center: 居中
    - tile: 平铺
    - stretch: 拉伸
    - fit: 适应（保持比例缩放到全屏）
    - fill: 填充（保持比例裁剪到全屏）
    """
    reg_values = STYLE_REGISTRY.get(style, STYLE_REGISTRY["fill"])
    try:
        import win32api, win32con
        import win32gui  # noqa: F401

        k = win32api.RegOpenKeyEx(
            win32con.HKEY_CURRENT_USER,
            "Control Panel\\Desktop",
            0, win32con.KEY_SET_VALUE
        )
        win32api.RegSetValueEx(k, "WallpaperStyle", 0, win32con.REG_SZ,
                               reg_values["WallpaperStyle"])
        win32api.RegSetValueEx(k, "TileWallpaper", 0, win32con.REG_SZ,
                               reg_values["TileWallpaper"])
        win32api.RegCloseKey(k)
        logger.info(f"Wallpaper style set: {style}")
    except ImportError:
        # 后备方案：用 ctypes 操作注册表
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Control Panel\Desktop",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ,
                          reg_values["WallpaperStyle"])
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ,
                          reg_values["TileWallpaper"])
        winreg.CloseKey(key)
    except Exception as e:
        logger.warning(f"Set wallpaper style failed (benign): {e}")


def set_wallpaper(image_path: str, date_str: str = None, style: str = "fill",
                  pos_x: int = 50, pos_y: int = 50, scale: int = 50) -> bool:
    """设置桌面壁纸

    Args:
        image_path: 图片文件路径
        date_str: 日期标识（可选，用于缓存命名）
        style: 壁纸样式: center | tile | stretch | fit | fill
        pos_x: 水平位置百分比 0-100（50=居中，仅自定义模式生效）
        pos_y: 垂直位置百分比 0-100（50=居中）
        scale: 缩放百分比 0-100（50=原始大小，0=极小，100=放大2倍）

    Returns:
        是否设置成功
    """
    if not image_path:
        return False

    try:
        if date_str:
            from config import get_wallpaper_path
            wp_path = get_wallpaper_path(date_str)
            shutil.copy2(image_path, wp_path)
            image_path = str(wp_path)

        # 自定义位置/缩放：合成到黑色画布，样式强制居中
        composed = compose_wallpaper(image_path, pos_x, pos_y, scale)
        if composed:
            # 合成后复制到壁纸缓存目录，用 center 样式
            if date_str:
                from config import get_wallpaper_path
                wp_path = get_wallpaper_path(date_str)
                shutil.copy2(composed, wp_path)
                image_path = str(wp_path)
            else:
                image_path = composed
            set_wallpaper_style("center")
        else:
            # 先设置壁纸样式
            set_wallpaper_style(style)

        # 再设置壁纸图片
        abs_path = str(image_path)
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, abs_path,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )
        logger.info(f"Wallpaper set (style={style}, pos={pos_x},{pos_y}, scale={scale}): {abs_path}")
        return True
    except Exception as e:
        logger.error(f"Set wallpaper failed: {e}")
        return False
