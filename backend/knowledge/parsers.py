"""文档解析：各格式 → 纯文本。解析失败返回 None（不中断索引）。
扫描版 PDF（文字提取为空但有内容）通过 VL 模型或 Windows 系统 OCR 补充。"""
import os

from html.parser import HTMLParser as _HTMLParser

from backend.core.scanner import TEXT_EXTS

# 解析管线版本：解析行为变化（PDF 主通道、docx 标题等）时 +1，
# indexer 据此让旧索引在下次索引时自动按新管线重解析
# v3: HTML 从「整文件按文本读」改为剥标签取正文；新增 eml 邮件解析
PARSE_VER = "3"

# 知识库支持的扩展名
SUPPORTED = {
    "pdf", "docx", "pptx", "xlsx", "doc", "md", "markdown", "txt", "csv",
} | TEXT_EXTS | {"eml"}


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
        if ext == "eml":
            return _parse_eml(path)
        if ext in ("html", "htm"):
            return _parse_html(path)
        if ext in TEXT_EXTS:
            return _parse_text(path)
        # doc（旧版二进制格式）python-docx 打不开时尝试按文本读
        if ext == "doc":
            return _parse_text(path) or None
        return None
    except Exception:
        return None


class _HTMLTextExtractor(_HTMLParser):
    """剥标签取正文：<script>/<style> 整段丢弃，块级标签换行，
    h1-h3 转 md 标题行（chunker 据此按章节切块）。"""

    _BLOCK = {"p", "div", "section", "article", "li", "tr", "table",
              "ul", "ol", "br", "blockquote", "pre", "header", "footer", "hr"}
    _HEAD = {"h1": "# ", "h2": "## ", "h3": "### "}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif self._skip:
            return
        elif tag in self._HEAD:
            self.parts.append("\n" + self._HEAD[tag])
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self._skip:
                self._skip -= 1
        elif not self._skip and tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data)


def _read_decoded(path, limit=2_000_000):
    """读文件前 limit 字节并解码：utf-8 → gb18030 逐级尝试
    （中文网页/邮件常见 gb2312 是 gb18030 子集）。"""
    with open(path, "rb") as f:
        raw = f.read(limit)
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _html_to_text(html):
    """HTML 字符串 → 压缩空行后的正文；解析失败返回 None。"""
    p = _HTMLTextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None
    lines = [ln.strip() for ln in "".join(p.parts).splitlines()]
    out = "\n".join(ln for ln in lines if ln)
    return out.strip() or None


def _parse_html(path):
    """HTML → 正文：剥 script/style 与标签（旧实现整文件按文本读，
    标签与脚本噪音全进向量与 FTS，检索质量明显受害）。"""
    return _html_to_text(_read_decoded(path))


def _parse_eml(path):
    """eml 邮件 → 主题/发件人/日期 + 各 text/plain 正文段；
    纯 HTML 邮件复用剥标签逻辑。附件（非 text 部分）不进正文。"""
    import email
    from email import policy
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    parts = []
    for key, label in (("Subject", "主题"), ("From", "发件人"), ("Date", "日期")):
        v = str(msg.get(key, "") or "").strip()
        if v:
            parts.append(f"{label}: {v}")
    bodies, html_bodies = [], []
    for part in msg.walk():
        if part.get_content_maintype() != "text":
            continue
        try:
            body = part.get_content()
        except Exception:
            continue
        if part.get_content_subtype() == "html":
            html_bodies.append(body)
        else:
            bodies.append(body)
    text = "\n\n".join(bodies).strip()
    if not text and html_bodies:
        text = (_html_to_text("\n".join(html_bodies)) or "").strip()
    if text:
        parts.append(text)
    return "\n\n".join(parts).strip() or None


def _parse_pdf(path):
    """主通道 PyMuPDF（阅读顺序/双栏/表格文本明显好于 pypdf，且已在依赖里）；
    fitz 打不开（加密/损坏）时 pypdf 兜底；两者都抽不出文字视为扫描件走 OCR。"""
    text = _pdf_text_fitz(path)
    if not text or len(text) < 20:
        text = _pdf_text_pypdf(path)
    if not text or len(text) < 20:
        return _ocr_scanned_pdf(path)
    return text


def _pdf_text_fitz(path):
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        try:
            return "\n\n".join((page.get_text(sort=True) or "") for page in doc).strip()
        finally:
            doc.close()
    except Exception:
        return ""


def _pdf_text_pypdf(path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
    except Exception:
        return ""


def _ocr_scanned_pdf(path):
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
    parts = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        # 标题样式输出为 md 风格标题行，chunker 据此按章节切块
        style = getattr(p.style, "name", "") or ""
        if style.startswith(("Heading", "标题")):
            parts.append("# " + t)
        else:
            parts.append(t)
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
