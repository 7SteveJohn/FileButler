"""Windows 系统内置 OCR（Windows.Media.Ocr，WinRT 投影）。

零模型依赖：Windows 10/11 自带 OCR 引擎，中文（zh-Hans-CN）在中文系统上
开箱可用（如不可用需在 系统设置→时间和语言→语言→选项 里添加"光学字符识别"）。
用途：图片文字搜索 / 扫描版 PDF 的无 AI 降级通路。

- API：https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr
- Python 投影：pywinrt（winrt-* 系列 pip 包，微软官方）
"""
import asyncio
import io
import re
import threading

# 引擎语言优先级：简中最常用，回退英文，再回退任意可用语言
_PREF = ("zh-Hans-CN", "zh-Hant-CN", "zh", "en-US")
_engine = None          # 缓存的 OcrEngine
_engine_lock = threading.Lock()
_loop = None            # WinRT 异步调用专用的常驻事件循环（daemon 线程）
_max_dim = None         # 引擎支持的最大图像边长（像素）
_avail = None           # 可用性探测缓存（False 时配合 _avail_reason，避免重复走 WinRT 互操作）
_avail_reason = ""


def _imports():
    """惰性导入 winrt 模块（未安装或非 Windows 时返回 None）。"""
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
        return Language, OcrEngine, BitmapDecoder, InMemoryRandomAccessStream, DataWriter
    except Exception:
        return None


def get_engine():
    """获取（并缓存）OCR 引擎。无可用语言包时返回 None。"""
    global _engine, _max_dim, _avail, _avail_reason
    if _engine is not None:
        return _engine
    if _avail is False:
        return None
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _avail is False:
            return None
        mods = _imports()
        if mods is None:
            _avail = False
            _avail_reason = "未安装 WinRT OCR 组件"
            return None
        Language, OcrEngine, _, _, _ = mods
        engine = None
        try:
            for tag in _PREF:
                if OcrEngine.is_language_supported(Language(tag)):
                    engine = OcrEngine.try_create_from_language(Language(tag))
                    if engine:
                        break
            if engine is None:
                engine = OcrEngine.try_create_from_user_profile_languages()
        except Exception:
            engine = None
        if engine is None:
            _avail = False
            _avail_reason = "系统缺少 OCR 语言包"
            return None
        try:
            _max_dim = OcrEngine.max_image_dimension or 10000
        except Exception:
            _max_dim = 10000
        _engine = engine
        _avail = True
        return _engine


def languages():
    """系统当前支持 OCR 的语言标签列表（设置页展示用）。"""
    mods = _imports()
    if mods is None:
        return []
    Language, OcrEngine, _, _, _ = mods
    out = []
    try:
        for tag in _PREF + ("ja", "ko", "en-GB", "fr-FR", "de-DE"):
            if OcrEngine.is_language_supported(Language(tag)):
                out.append(tag)
    except Exception:
        pass
    return out


def available():
    return get_engine() is not None


def status():
    """可用性 + 不可用原因（前端菜单置灰、设置页展示用）。探测结果已缓存。"""
    ok = available()
    return {"available": ok,
            "reason": "" if ok else (_avail_reason or "系统 OCR 不可用"),
            "hint": "" if ok else "请在 系统设置→时间和语言→语言→选项 中添加「光学字符识别」"}


# ---------- 常驻事件循环（WinRT 异步 API 用） ----------

def _run_loop():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _ensure_loop():
    global _loop
    if _loop is None:
        threading.Thread(target=_run_loop, daemon=True, name="fb-winocr").start()
        while _loop is None:
            import time as _t
            _t.sleep(0.01)
    return _loop


def _await(coro, timeout=60):
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop()).result(timeout)


async def _ocr_png_bytes(engine, png_bytes):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png_bytes)
    await writer.store_async()
    decoder = await BitmapDecoder.create_async(stream)
    bmp = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bmp)
    lines = []
    for line in result.lines:
        lines.append(_normalize(line.text))
    return "\n".join(lines)


def _normalize(text):
    """OCR 会给汉字之间插空格（'发 票 号 码'），导致 '发票号' 关键词搜不到。
    去掉 CJK 字符之间的空格；行首尾空格一并清理。"""
    t = text.strip()
    t = re.sub(r'(?<=[\u4e00-\u9fff\u3400-\u4dbf])\s+(?=[\u4e00-\u9fff\u3400-\u4dbf])', '', t)
    return t


def ocr_image(pil_im):
    """PIL.Image → 识别文本（空串表示没识别到）。超大图自动缩到引擎上限内。
    任何失败抛异常，由调用方决定降级行为。"""
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Windows OCR 不可用（缺少语言包）")
    im = pil_im
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    limit = _max_dim or 10000
    if max(w, h) > limit:  # 保持比例缩到上限内
        scale = limit / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return _await(_ocr_png_bytes(engine, buf.getvalue())).strip()


def ocr_file(path):
    """图片文件 → 识别文本。"""
    from PIL import Image
    with Image.open(path) as im:
        return ocr_image(im)


def ocr_pdf(path, max_pages=30, dpi=200, page_cb=None):
    """扫描版 PDF → 文本：PyMuPDF 逐页渲染 → Windows OCR。
    返回 None 表示完全没识别到内容。page_cb(第几页, 总页数) 供进度展示。"""
    engine = get_engine()
    if engine is None:
        return None
    import fitz  # PyMuPDF（requirements 已有）
    doc = fitz.open(path)
    try:
        n = min(len(doc), max_pages)
        texts = []
        for i in range(n):
            if page_cb:
                page_cb(i + 1, n)
            pix = doc[i].get_pixmap(dpi=dpi)
            text = _await(_ocr_png_bytes(engine, pix.tobytes("png")), timeout=120).strip()
            if text:
                texts.append(f"[第{i + 1}页]\n{text}")
        return "\n\n".join(texts).strip() or None
    finally:
        doc.close()
