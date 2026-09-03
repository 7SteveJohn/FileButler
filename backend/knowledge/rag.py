"""RAG 问答：向量检索 → 拼装上下文 → 本地模型流式生成，附引用来源。"""
from backend import db
from backend import llm
from backend.ollama_client import OllamaNotRunning

SYSTEM_PROMPT = (
    "你是本地知识库问答助手。请只依据用户提供的「资料」回答问题，用中文回答。"
    "回答末尾列出你引用的资料编号，格式如：来源 [1][3]。"
    "如果资料不足以回答，直接说明知识库中没有相关内容，不要编造。"
)

QA_TEMPLATE = """资料：
{context}

问题：{question}"""


def _search_images(client, qv, k):
    """图片语义检索（img_index，描述+OCR 文字），返回 [{type:'image', ...}]。"""
    hits = db.img_vec_index.search(qv, k=k)
    if not hits:
        return []
    rows = db.img_rows_by_rowids([h[0] for h in hits])
    out = []
    for rid, score in hits:
        r = rows.get(rid)
        if r:
            text = r["descr"] + (f"｜图中文字：{r['ocr']}" if r.get("ocr") else "")
            out.append({"type": "image", "file_path": r["path"],
                        "text": text, "descr": r["descr"],
                        "ocr": r.get("ocr", ""), "score": round(score, 4)})
    return out


def search(query, k=8):
    """语义检索（文档 + 图片描述/OCR 融合）；向量模型不可用时降级为关键词匹配。"""
    client = llm.get_embed_client()
    results = []
    images = []
    mode = "vector"
    if llm.embed_available():
        try:
            qv = client.embed_one(query)
            hits = db.vec_index.search(qv, k=k)
            if hits:
                rows = db.search_chunks_by_ids([h[0] for h in hits])
                for cid, score in hits:
                    r = rows.get(cid)
                    if r:
                        results.append({**r, "score": round(score, 4)})
            images = _search_images(llm.get_embed_client(), qv, k=4)
        except Exception:
            mode = "keyword"
    else:
        mode = "keyword"
    if mode == "keyword":
        results = db.keyword_search(query, k=k)
        images = [{"type": "image", "file_path": r["path"], "text": r["descr"],
                   "descr": r["descr"], "ocr": r.get("ocr", ""), "score": 0}
                  for r in db.img_keyword_search(query, k=4)]
    return {"mode": mode, "results": results, "images": images}


def ask(query, k=8, stream_cb=None, history=None):
    """
    知识库问答。stream_cb(token) 逐段回调；返回 {"answer", "sources", "mode"}。
    """
    client = llm.get_chat_client()
    if not llm.chat_available():
        raise OllamaNotRunning("对话模型不可用：" + llm.chat_status()["reason"])

    embed_client = llm.get_embed_client()
    if llm.embed_available():
        qv = embed_client.embed_one(query)
        hits = db.vec_index.search(qv, k=k)
        rows = db.search_chunks_by_ids([h[0] for h in hits]) if hits else {}
        picked = [(h[1], rows[h[0]]) for h in hits if h[0] in rows]
        # 注意：不再把 img_index（图片描述）混入 KB 问答——
        # 用户只加文本文件夹时，QQMusic 封面/应用图标等全盘图片描述会污染答案
        # 图片语义检索走独立的 search() API（Files 页"图片语义搜索"）
    else:
        picked = [(0.0, r) for r in db.keyword_search(query, k=k)]

    # list intent 优先：用户问"知识库里有什么文件/什么文档"时直接返回文件清单
    # 防止旧残留索引（如 README.md/截图等）被命中回答，跑题
    if _is_list_intent(query):
        listing = _list_kb_files(limit=20)
        if listing:
            answer = "知识库里已有以下文件（前 20 个）：\n" + "\n".join(
                f"  - {p}" for p in listing)
        else:
            answer = "知识库里还没有任何文件（点「+ 添加文件夹」索引文档后即可问答）。"
        if stream_cb:
            stream_cb(answer)
        return {"answer": answer, "sources": [], "mode": "list"}

    sources = []
    if not picked:
        # 0 命中 fallback：用户问"什么文件/什么文档"时直接返回 KB 文件清单
        # 否则提示"未检索到"
        if _is_list_intent(query):
            listing = _list_kb_files(limit=20)
            if listing:
                answer = "知识库里已有以下文件（前 20 个）：\n" + "\n".join(
                    f"  - {p}" for p in listing)
            else:
                answer = "知识库里还没有任何文件（点「+ 添加文件夹」索引文档后即可问答）。"
        else:
            answer = "知识库中没有检索到与问题相关的内容。可以先确认相关文件已加入知识库并完成索引，或者换个说法再问一次。"
        if stream_cb:
            stream_cb(answer)
        return {"answer": answer, "sources": [], "mode": "keyword"}

    ctx_parts = []
    for i, (score, row) in enumerate(picked, 1):
        snippet = row["text"][:600]
        ctx_parts.append(f"[{i}] (来源: {row['file_path']})\n{snippet}")
        sources.append({
            "index": i,
            "file": row["file_path"],
            "score": round(score, 4),
            "snippet": row["text"][:200],
        })

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:  # 保留最近 3 轮对话
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": QA_TEMPLATE.format(
        context="\n\n".join(ctx_parts), question=query)})

    answer = client.chat(messages, stream_cb=stream_cb, options={"temperature": 0.3})
    return {"answer": answer, "sources": sources, "mode": "vector" if llm.embed_available() else "keyword"}


def _is_list_intent(query):
    """用户是否想问"知识库里有什么"（列清单意图）。"""
    if not query:
        return False
    q = query.strip().lower()
    triggers = ("什么文件", "什么文档", "哪些文件", "哪些文档", "有那些",
                "有哪些", "列出来", "列一下", "列表", "list", "all files",
                "全部文件", "清单", "看过什么")
    return any(t in q for t in triggers)


def _list_kb_files(limit=20):
    """返回知识库里已索引的文件路径列表（去重 + 按字母序）。"""
    import os
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT path FROM kb_files WHERE status='ok' ORDER BY path"
        ).fetchall()
    finally:
        conn.close()
    return [r["path"] for r in rows[:limit]]
