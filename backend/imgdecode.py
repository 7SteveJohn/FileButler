"""图片解码统一入口。

HEIC/HEIF 是 iPhone 默认格式，PIL 自身打不开，需要 pillow-heif 注册解码器。
所有要读像素的地方（缩略图、预览、EXIF 拍摄时间、dHash 相似图、视觉模型、
系统 OCR）都从这里取图，避免各自写一遍 heic 分支、也避免某处漏掉导致
「同一张照片在图库能用、在相似图里失效」。

未安装 pillow-heif 时不抛异常：HEIC 打不开（返回 None / 由调用方按失败处理），
其它格式完全不受影响。
"""
import io
import os

_registered = False


def _register():
    """首次调用时注册 HEIF 解码器。"""
    global _registered
    if _registered:
        return
    _registered = True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:
        pass    # 没装 pillow-heif：HEIC 不可读，其余格式照常


def is_heic(path):
    return os.path.splitext(path)[1].lstrip(".").lower() in ("heic", "heif")


def needs_jpeg_transcode(path):
    """浏览器或视觉模型原生不支持、需转码为 JPEG 的图片后缀（HEIC/HEIF/TIFF）。"""
    return os.path.splitext(path)[1].lstrip(".").lower() in ("heic", "heif", "tif", "tiff")


def exif_datetime_str(path):
    """读取图片 EXIF 拍摄时间原始字符串（如 '2021:05:04 12:30:00'）；无则返回 None。

    注意：Pillow 10+ 的公开 getexif() 顶层仅暴露 IFD0，而标准 EXIF 2.3 中相机/手机
    写入的 DateTimeOriginal(36867) 与 DateTimeDigitized(36868) 均存放在 Exif Sub-IFD
    (0x8769) 内，必须先查 get_ifd(0x8769)，再回退顶层 36867/36868/306(DateTime)。
    """
    try:
        with open_image(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            raw = None
            try:
                sub = exif.get_ifd(0x8769) or {}
                raw = sub.get(36867) or sub.get(36868)
            except Exception:
                raw = None
            if not raw:
                raw = exif.get(36867) or exif.get(36868) or exif.get(306)
            if not raw:
                return None
            s = str(raw).strip().rstrip("\x00").strip()
            return s or None
    except Exception:
        return None


def open_image(path):
    """打开为 PIL Image（含 HEIC）。调用方负责 close / with。"""
    _register()
    from PIL import Image
    return Image.open(path)


def jpeg_bytes(path, max_side=2000, quality=88):
    """转成 JPEG 字节：视觉模型与系统 OCR 都不吃 HEIC，必须转码。

    按 EXIF 方向矫正，长边限制在 max_side 内（12MP 原图转 base64 会给
    Ollama 塞几 MB 无用数据）；调用方负责处理异常。
    """
    _register()
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
