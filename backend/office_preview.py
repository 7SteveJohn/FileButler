"""Office 文档预览：docx/xlsx/pptx → HTML（供内置预览窗口渲染）。

只提取文字与表格内容，所有文本经 HTML 转义后输出，绝不执行任何脚本；
渲染大文件时限制行/列/页数，保证秒开。
"""
import html


def _esc(s):
    return html.escape(str(s))


def docx_to_html(path):
    """Word：段落（保留标题层级）+ 表格。"""
    from docx import Document
    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        # 部分段落（表格内、图片占位等）的 style 可能为 None
        style = ((p.style.name if p.style else "") or "").lower()
        if "heading 1" in style:
            parts.append(f"<h1>{_esc(t)}</h1>")
        elif "heading 2" in style:
            parts.append(f"<h2>{_esc(t)}</h2>")
        elif "heading 3" in style:
            parts.append(f"<h3>{_esc(t)}</h3>")
        elif "title" in style:
            parts.append(f"<h1>{_esc(t)}</h1>")
        else:
            parts.append(f"<p>{_esc(t)}</p>")
    for table in doc.tables[:10]:
        rows = []
        for r in table.rows[:200]:
            cells = [_esc(c.text.strip()) for c in r.cells]
            if any(cells):
                rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        if rows:
            parts.append("<table>" + "".join(rows) + "</table>")
    body = "".join(parts) or "<p class='fb-office-empty'>（文档没有可提取的文字内容）</p>"
    return f'<div class="fb-office">{body}</div>'


def xlsx_to_html(path):
    """Excel：每个工作表一张表（最多 200 行 × 30 列，最多 10 个工作表）。"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets[:10]:
            rows = []
            for i, row in enumerate(ws.iter_rows(max_row=300, max_col=30)):
                if i >= 200:
                    break
                cells = [_esc(c.value) if c.value is not None else "" for c in row]
                if any(cells):
                    rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            if rows:
                parts.append(f"<h3>{_esc(ws.title)}</h3><table>" + "".join(rows) + "</table>")
    finally:
        wb.close()
    body = "".join(parts) or "<p class='fb-office-empty'>（工作簿没有可显示的内容）</p>"
    return f'<div class="fb-office">{body}</div>'


def pptx_to_html(path):
    """PPT：逐页列出形状文本与表格（最多 100 页）。"""
    from pptx import Presentation
    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides):
        if i >= 100:
            break
        texts = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(f"<p>{_esc(t)}</p>")
            if getattr(shape, "has_table", False) and shape.has_table:
                for r in shape.table.rows[:50]:
                    cells = [_esc(c.text.strip()) for c in r.cells]
                    if any(cells):
                        texts.append("<p>　" + " ｜ ".join(cells) + "</p>")
        if texts:
            parts.append(f"<h4>第 {i + 1} 页</h4>" + "".join(texts))
    body = "".join(parts) or "<p class='fb-office-empty'>（演示文稿没有可提取的文字内容）</p>"
    return f'<div class="fb-office">{body}</div>'


ARCHIVE_EXTS = ("zip", "tar", "gz", "tgz", "bz2", "xz")


def _fmt_bytes(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return "0 B"


def archive_to_html(path, ext=""):
    """压缩包只读目录预览（zip / tar / tgz / tar.gz / tar.bz2 / tar.xz）：
    仅读取归档头信息，最多展示 300 条，绝不解压落盘（无 Zip Bomb 风险）。"""
    import os
    import tarfile
    import zipfile

    ext = (ext or os.path.splitext(path)[1]).lstrip(".").lower()
    entries = []  # (name, size, mtime_str, is_dir)
    total_files = 0
    total_dirs = 0
    total_size = 0

    if ext == "zip":
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                is_dir = info.is_dir() if hasattr(info, "is_dir") else info.filename.endswith("/")
                if is_dir:
                    total_dirs += 1
                else:
                    total_files += 1
                    total_size += int(info.file_size or 0)
                if len(entries) < 300:
                    dt = info.date_time
                    ts = f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}" if dt else ""
                    entries.append((info.filename, info.file_size, ts, is_dir))
    else:
        try:
            with tarfile.open(path, "r:*") as tf:
                import datetime as _dt
                for m in tf:
                    is_dir = m.isdir()
                    if is_dir:
                        total_dirs += 1
                    else:
                        total_files += 1
                        total_size += int(m.size or 0)
                    if len(entries) < 300:
                        ts = (_dt.datetime.fromtimestamp(m.mtime).strftime("%Y-%m-%d %H:%M")
                              if getattr(m, "mtime", 0) else "")
                        name = m.name + ("/" if is_dir and not m.name.endswith("/") else "")
                        entries.append((name, m.size, ts, is_dir))
        except tarfile.ReadError:
            # 单文件 .gz/.bz2/.xz 流（非 tar 归档）
            st_size = os.path.getsize(path)
            stem = os.path.basename(path).rsplit(".", 1)[0] or os.path.basename(path)
            body = (f"<p>单文件压缩流：<b>{_esc(stem)}</b>（压缩体积 {_fmt_bytes(st_size)}）</p>"
                    "<p class='fb-office-empty'>（非归档容器，需用解压软件查看内容）</p>")
            return f'<div class="fb-office">{body}</div>'

    if not entries:
        return '<div class="fb-office"><p class="fb-office-empty">（空压缩包）</p></div>'

    summary = (f"<p>共 <b>{total_files}</b> 个文件"
               + (f"、<b>{total_dirs}</b> 个目录" if total_dirs else "")
               + f"，解压后总大小 <b>{_fmt_bytes(total_size)}</b></p>")
    rows = ["<tr><th>路径</th><th>大小</th><th>修改时间</th></tr>"]
    for name, sz, ts, is_dir in entries:
        sz_txt = "—" if is_dir else _fmt_bytes(sz)
        rows.append(f"<tr><td>{_esc(name)}</td><td>{_esc(sz_txt)}</td><td>{_esc(ts)}</td></tr>")
    total_all = total_files + total_dirs
    if total_all > len(entries):
        rows.append(f"<tr><td colspan='3'>… 其余 {total_all - len(entries)} 项已省略</td></tr>")
    return f'<div class="fb-office">{summary}<table>{"".join(rows)}</table></div>'


def to_html(path, ext):
    """按扩展名分发；不支持的返回 None。"""
    ext = ext.lstrip(".").lower()
    if ext == "docx":
        return docx_to_html(path)
    if ext == "xlsx":
        return xlsx_to_html(path)
    if ext == "pptx":
        return pptx_to_html(path)
    if ext in ARCHIVE_EXTS:
        return archive_to_html(path, ext)
    return None
