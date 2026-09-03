"""图片语义搜索：视觉模型（qwen2.5vl）生成图片描述 → bge-m3 向量化 → img_index 表。

与文档知识库分开存放，检索时由 rag 融合；搜索「日落」能翻出描述里含日落的照片。
"""
import os

from backend import db, fileindex
from backend import llm

VL_MODEL = "qwen2.5vl:7b"


def semantic_enabled():
    return db.get_setting("img_semantic", "0") == "1"


def set_semantic(enabled):
    db.set_setting("img_semantic", "1" if enabled else "0")


def vl_model():
    return db.get_setting("vl_model", VL_MODEL)


def vl_ready(client=None):
    """视觉模型就绪判断：API 模式只要对话可用即视为可尝试；本地模式查模型。"""
    if llm.mode() == "api":
        return llm.chat_available()
    client = client or llm.get_chat_client()
    try:
        return client.has_model(vl_model())
    except Exception:
        return False


def describe_files(paths, progress_cb=None):
    """为图片列表生成描述并入库（已描述且未变化的跳过）。
    视觉模型就绪 → 描述+OCR+向量；模型缺失但 Windows OCR 可用 → 仅 OCR
    （descr 空、无向量，关键词图片搜索「搜发票号找截图」照常可用，零模型依赖）。"""
    paths = [p for p in paths
             if os.path.splitext(p)[1].lstrip(".").lower() in fileindex.IMAGE_EXTS
             and os.path.exists(p)]
    if not paths:
        return {"described": 0, "skipped": 0, "failed": 0}

    chat_client = llm.get_chat_client()
    vl_ok = not (llm.mode() == "ollama" and not chat_client.has_model(vl_model()))
    if vl_ok and not llm.embed_available():
        vl_ok = False  # 没有向量模型时描述无法向量化，走 OCR-only

    from backend.knowledge import winocr
    if not vl_ok and not winocr.available():
        return {"error": "视觉模型未安装且系统 OCR 不可用（可在设置页下载视觉模型，"
                         "或在系统设置→语言 中添加 OCR 语言包）"}

    client = chat_client  # 统一命名，供循环内 describe_image / embed_one 使用
    existing = db.img_rows_by_paths(paths)
    described = skipped = failed = ocr_only = 0
    for i, path in enumerate(paths):
        if progress_cb:
            progress_cb(i + 1, len(paths), os.path.basename(path))
        try:
            stt = os.stat(path)
        except OSError:
            continue
        old = existing.get(path)
        if old and abs(old["mtime"] - stt.st_mtime) < 1:
            skipped += 1
            continue
        try:
            if vl_ok:
                desc, ocr = client.describe_image(path, vl_model())
                if not desc:
                    failed += 1
                    continue
                vec = client.embed_one(
                    f"图片描述：{desc}\n图中文字：{ocr}" if ocr else f"图片描述：{desc}")
                db.upsert_img(path, stt.st_mtime, stt.st_size, desc, vec, ocr=ocr or "")
                described += 1
            else:
                # 零模型降级：Windows 系统内置 OCR，只填 ocr 列（无描述/向量）
                text = winocr.ocr_file(path)
                db.upsert_img(path, stt.st_mtime, stt.st_size, "", None,
                              ocr=text or "")
                if text:
                    ocr_only += 1
                else:
                    skipped += 1  # 图里本来就没字，不算失败
        except Exception:
            failed += 1
    db.img_vec_index.invalidate()
    result = {"described": described, "skipped": skipped, "failed": failed}
    if ocr_only:
        result["ocr_only"] = ocr_only  # 前端可提示"已用系统OCR识别N张图中的文字"
    return result


def pending_images(limit=100000):
    """文件总索引里尚未描述（或已过期）的图片路径。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT f.path, f.mtime FROM file_index f "
            "LEFT JOIN img_index i ON i.path = f.path "
            "WHERE f.ext IN ('jpg','jpeg','png','gif','bmp','webp') "
            "AND (i.path IS NULL OR ABS(i.mtime - f.mtime) >= 1) "
            "LIMIT ?", (limit,)).fetchall()
        return [r["path"] for r in rows]
    finally:
        conn.close()
