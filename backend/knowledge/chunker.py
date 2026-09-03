"""文本切块：按段落聚合，约 800 字符一块，相邻块重叠 120 字符。"""


def chunk_text(text, target=800, overlap=120):
    """返回文本块列表。中文场景按字符切分即可保持语义完整。"""
    text = text.strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks = []
    buf = ""
    for p in paras:
        # 超长段落硬切
        while len(p) > target * 2:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:target * 2])
            p = p[target * 2:]
        if len(buf) + len(p) + 1 <= target:
            buf = (buf + "\n" + p) if buf else p
        else:
            if buf:
                chunks.append(buf)
            # 携带上一块尾部作为重叠上下文
            buf = (chunks[-1][-overlap:] + "\n" + p) if chunks and overlap else p
            if len(buf) > target * 2:
                chunks.append(buf[:target * 2])
                buf = buf[target * 2:]
    if buf:
        chunks.append(buf)
    return chunks
