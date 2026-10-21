"""文本切块：按段落聚合，约 800 字符一块，相邻块重叠 120 字符。

Markdown 标题行（# 开头）与幻灯片页标记（[第N页]）是硬边界；每块首行前置
「文件名 · 所在标题」，块向量因此携带文档与章节上下文——跨块指代、
检索排序都受益（成熟 RAG 的标准做法，无额外成本）。title 传文件名即可。"""


def chunk_text(text, target=800, overlap=120, title=""):
    """返回文本块列表。中文场景按字符切分即可保持语义完整。"""
    text = text.strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    raw = []      # (内容, 所在标题)；内容不含前缀，最后统一加
    buf = ""
    heading = ""

    def flush():
        nonlocal buf
        if buf:
            raw.append((buf, heading))
            buf = ""

    for p in paras:
        if p.startswith("#") or p.startswith("[第"):
            flush()
            heading = p.lstrip("#").strip()
            continue
        # 超长段落硬切
        while len(p) > target * 2:
            if buf:
                flush()
            raw.append((p[:target * 2], heading))
            p = p[target * 2:]
        if len(buf) + len(p) + 1 <= target:
            buf = (buf + "\n" + p) if buf else p
        else:
            flush()
            # 携带上一块尾部作为重叠上下文
            buf = (raw[-1][0][-overlap:] + "\n" + p) if raw and overlap else p
            if len(buf) > target * 2:
                raw.append((buf[:target * 2], heading))
                buf = buf[target * 2:]
    flush()

    out = []
    for content, h in raw:
        head = f"{title} · {h}" if h else title
        out.append((head + "\n" + content) if head else content)
    return out
