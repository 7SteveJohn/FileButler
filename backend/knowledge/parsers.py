"""文档解析：各格式 → 纯文本。解析失败返回 None（不中断索引）。
扫描版 PDF（文字提取为空但有内容）通过 VL 模型或 Windows 系统 OCR 补充。"""
import os

from backend.core.scanner import TEXT_EXTS

# 知识库支持的扩展名
SUPPORTED = {
    "pdf", "docx", "pptx", "xlsx", "doc", "md", "markdown", "txt", "csv",
} | TEXT_EXTS


def parse_file(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    try:
        if ext == "pdf":
            return _parse_pdf(path)
        if ext == "docx":
            return _parse_docx(path)
        if ext == "pptx":
            return _parse_pptx(path)
        if ext == "xlsx":
            return _parse_xlsx(path)
        if ext == "csv":
            return _parse_csv(path)
        if ext in TEXT_EXTS:
            return _parse_text(path)
        # doc（旧版二进制格式）python-docx 打不开时尝试按文本读
        if ext == "doc":
            return _parse_text(path) or None
        return None
    except Exception:
        return None


def _parse_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = [p.extract_text() or "" for p in reader.pages]
    text = "\n\n".join(pages).strip()
    # 扫描件检测：文本极少但文件较大 → 可能是扫描版
    if not text or len(text) < 20:
        return _ocr_scanned_pdf(path, pages)
    return text or None


def _ocr_scanned_pdf(path, original_pages):
    """扫描版 PDF OCR：Windows 系统 OCR 优先（实测 ~0.3s/页、无需模型、
    中文识别质量好）；系统 OCR 不可用时回退 VL 模型。两者都不可用返回 None。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        from backend.knowledge import winocr
        if winocr.available():
            return winocr.ocr_pdf(path, max_pages=30, dpi=200)
        # 回退：VL 模型逐页转录（需 qwen2.5vl；慢且依赖模型就绪）
        from backend import llm
        from backend.knowledge import imgsearch
        if llm.chat_available() and imgsearch.vl_ready():
            return _ocr_pdf_by_vl(path, llm, imgsearch)
        return None
    except Exception:
        return None


def _ocr_pdf_by_vl(path, llm, imgsearch):
    client = llm.get_chat_client()
    vl = imgsearch.vl_model()
    import fitz
    import base64
    doc = fitz.open(path)
    all_text = []
    try:
        for page_num in range(min(len(doc), 30)):  # 最多 30 页
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_data = pix.tobytes("png")
            b64 = base64.b64encode(img_data).decode("ascii")
            try:
                r = client.chat(
                    messages=[{
                        "role": "user",
                        "content": "请逐字转录此页中的所有文字，保持原始排版格式。如果没有文字则回复「无」。",
                        "images": [b64],
                    }],
                    model=vl,
                    options={"temperature": 0.1, "num_predict": 1024},
                    think=False,
                )
                if r and r.strip() != "无":
                    all_text.append(f"[第{page_num + 1}页]\n{r.strip()}")
            except Exception:
                continue
        return "\n\n".join(all_text).strip() or None
    finally:
        doc.close()


def _parse_docx(path):
    import docx
    doc = docx.Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip() or None


def _parse_pptx(path):
    from pptx import Presentation
    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        texts.append(" | ".join(cells))
        if texts:
            parts.append(f"[第{i}页]\n" + "\n".join(texts))
    return "\n\n".join(parts).strip() or None


def _parse_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                lines.append(" | ".join(cells))
            if len(lines) >= 500:  # 超大表截断
                break
        if lines:
            parts.append(f"[工作表 {ws.title}]\n" + "\n".join(lines))
    wb.close()
    return "\n".join(parts).strip() or None


def _parse_csv(path):
    return _parse_text(path)


def _parse_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read(2_000_000)  # 单文件最多读 2MB 文本
    return text.strip() or None
