"""RAG 问答：多轮改写 → 混合检索（向量 + FTS 词法，RRF 融合，可选 rerank 精排）→ 拼装上下文 → 本地模型流式生成。

检索通道降级链：hybrid（向量+FTS 融合）→ vector / fts 单路 → keyword（LIKE 兜底）；
hybrid 之上可叠 rerank（设置页开关，默认关）——重排失败自动回退 RRF 原序。
FTS 词法通道不依赖任何模型：没装 Ollama 时「问答/检索」仍可按词法命中并引用原文。
带 history 的追问先用一次小模型调用把新问题改写成自包含检索词（失败一律回退原词）；
返回结果附 rewritten_query 与 cited（校验过的引用编号），sources 带所在标题与页码/工作表定位。
"""
import re

from backend import db
from backend import llm
from backend.knowledge import reranker
from backend.ollama_client import OllamaNotRunning

SYSTEM_PROMPT = (
    "你是本地知识库问答助手。请只依据用户提供的「资料」回答问题，用中文回答。"
    "回答末尾列出你引用的资料编号，格式如：来源 [1][3]。"
    "如果资料不足以回答，直接说明知识库中没有相关内容，不要编造。"
)

QA_TEMPLATE = """资料：
{context}

问题：{question}"""

RRF_K = 60           # RRF 平滑常数：rank 差异对权重的区分度（成熟 RAG 通用值）
FUSE_CHANNEL_K = 24  # 每路召回条数；融合后取 top-k
RERANK_POOL = 50     # rerank 开启时融合后进入精排的候选数（精排完取 top-k）

REWRITE_MAX_CHARS = 100  # 改写结果超过该长度视为跑题输出，回退原 query

REWRITE_SYSTEM = (
    "你是检索查询改写器。结合对话上下文，把用户最新问题改写成一条自包含、"
    "不依赖上下文也能检索的查询：代词（它/这个/上面提到的）必须替换成具体对象。"
    "只输出改写后的查询文本，不要解释、引号或任何前缀。"
)

_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_PAGE_RE = re.compile(r"\[第(\d+)页\]")
_SHEET_RE = re.compile(r"\[工作表\s+([^\]\n]+)\]")


def _rrf_fuse(channels, k):
    """多路检索结果 RRF 融合。channels 为有序 id 列表（各自按相关性降序），
    返回 [(id, score)] 按 score 降序、截前 k。"""
    scores = {}
    for ch in channels:
        for pos, cid in enumerate(ch):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
    return sorted(scores.items(), key=lambda x: -x[1])[:k]


def rrf_merge_by_file(channels, k=RRF_K):
    """文件级多路结果 RRF 融合（global_search 的「全盘正文 FTS + 知识库块」
    两路用；旧逻辑是正文 FTS 恒排知识库前的拼接，知识库强命中永远被压住）。

    channels: 各路已按相关性排序的行列表，行需带 file_path。同文件在一路内
    只计最好名次（知识库一路可能同文件多块），代表行取该路先出现者——
    保留其展示形态（正文索引行带标记片段，知识库行带块原文）。"""
    scores, rep = {}, {}
    for ch in channels:
        rank, seen = 0, set()
        for r in ch:
            p = r.get("file_path")
            if not p or p in seen:
                continue
            seen.add(p)
            scores[p] = scores.get(p, 0.0) + 1.0 / (k + rank + 1)
            rep.setdefault(p, r)
            rank += 1
    return [rep[p] for p, _ in sorted(scores.items(), key=lambda x: -x[1])]


def _retrieve(query, k):
    """混合检索：返回 (picked, mode, query_vec)。
    picked 为 [(chunk_id, score)]，score 仅用于展示排序（hybrid 时是 RRF 分，
    vector 单路时是余弦，fts 单路时为 0）；query_vec 供图片语义通道复用，
    嵌入不可用时为 None。mode ∈ hybrid / vector / fts / keyword。"""
    vec_hits = []
    query_vec = None
    if llm.embed_available():
        try:
            query_vec = llm.get_embed_client().embed_one(query)
            vec_hits = db.vec_index.search(query_vec, k=max(k, FUSE_CHANNEL_K))
        except Exception:
            vec_hits = []
    try:
        fts_ids = db.chunk_fts_search(query, k=max(k, FUSE_CHANNEL_K))
    except Exception:
        fts_ids = []

    if vec_hits and fts_ids:
        return _rrf_fuse([[h[0] for h in vec_hits], fts_ids], k), "hybrid", query_vec
    if vec_hits:
        return vec_hits[:k], "vector", query_vec
    if fts_ids:
        return [(cid, 0.0) for cid in fts_ids[:k]], "fts", query_vec
    return [], "keyword", query_vec


def _rows_in_order(picked):
    """chunk id 列表 → {id: row}，保持 picked 顺序。"""
    ids = [cid for cid, s in picked]
    rows = db.search_chunks_by_ids(ids) if ids else {}
    return rows


def _apply_rerank(query, picked, rows, k):
    """rerank 精排：RRF 名次 → 读原文内容打分重排取前 k。

    RRF 只看两路给的「名次」，rerank 读块原文——对「两路都排进了但先后
    不对」的题是最后一档收益。失败/不可用一律回退 RRF 原序，绝不中断。"""
    ids = [cid for cid, _ in picked if cid in rows]
    if len(ids) < 2:
        return picked[:k]
    scores = reranker.rerank_scores(query, [rows[cid]["text"] or "" for cid in ids])
    if not scores:
        return picked[:k]
    order = sorted(range(len(ids)), key=lambda i: -scores[i])[:k]
    return [(ids[i], scores[i]) for i in order]


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
    """混合检索（文档 + 图片描述/OCR 融合）；模型与 FTS 全不可用时降级 LIKE。
    rerank 开启且可用时融合池扩到 50 精排回 top-k，结果附 reranked 标记。"""
    rerank_on = reranker.active()
    picked, mode, qv = _retrieve(query, RERANK_POOL if rerank_on else k)
    results = []
    reranked = False
    if picked:
        rows = _rows_in_order(picked)
        if rerank_on:
            picked2 = _apply_rerank(query, picked, rows, k)
            reranked = picked2 != picked[:k]
            picked = picked2
        for cid, score in picked:
            r = rows.get(cid)
            if r:
                results.append({**r, "score": round(score, 4)})
    if mode == "keyword":
        results = db.keyword_search(query, k=k)

    images = []
    if qv is not None:
        try:
            images = _search_images(llm.get_embed_client(), qv, k=4)
        except Exception:
            images = []
    else:
        images = [{"type": "image", "file_path": r["path"], "text": r["descr"],
                   "descr": r["descr"], "ocr": r.get("ocr", ""), "score": 0}
                  for r in db.img_keyword_search(query, k=4)]
    return {"mode": mode, "results": results, "images": images,
            "reranked": reranked}


def _rewrite_query(client, query, history):
    """多轮改写：最近对话 + 新问题 → 一条自包含检索 query（非流式小调用）。
    返回 None 表示改写失败，调用方回退原 query——任何异常、输出为空、
    超过 REWRITE_MAX_CHARS 都按失败处理，绝不让问答因此中断。"""
    msgs = [{"role": "system", "content": REWRITE_SYSTEM}]
    for h in (history or [])[-6:]:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": query})
    try:
        # num_predict 60：改写产物是一条检索词（几十 token 内），给太多配额
        # 只会让跑偏的输出拖更久——本机 8B 模型这步省下的就是体感延迟
        out = client.chat(msgs, options={"temperature": 0, "num_predict": 60},
                          think=False)
    except Exception:
        return None
    out = (out or "").strip()
    if not out:
        return None
    # 模型偶尔裹引号或多行输出：取首行并剥掉包裹引号
    out = out.splitlines()[0].strip().strip("「」“”\"'").strip()
    if not out or len(out) > REWRITE_MAX_CHARS:
        return None
    return out


def _split_prefix(text):
    """块文本首行是 chunker 统一加的「文件名 · 所在标题」前缀行，
    返回 (所在标题, 去前缀正文)：标题取「 · 」后半段，无标题为空串。
    单行块视为无前缀，整体原样返回。"""
    first, nl, body = text.partition("\n")
    if not nl:
        return "", text
    return (first.partition(" · ")[2].strip() if " · " in first else ""), body


def _locator(text):
    """从块文本解析位置标记：[第N页] → "第N页"，[工作表 X] → "工作表 X"。
    页标记经 chunker 成为前缀标题的一部分，所以全文范围查找。无标记返回空串。"""
    m = _PAGE_RE.search(text)
    if m:
        return f"第{m.group(1)}页"
    m = _SHEET_RE.search(text)
    if m:
        return "工作表 " + m.group(1).strip()
    return ""


def _parse_citations(answer, max_index):
    """解析答案末尾「来源 [n]」的引用编号：只认最后一个「来源」之后的部分，
    过滤越界编号（<1 或 >资料条数）并去重；没有「来源」标记视为无引用。"""
    pos = (answer or "").rfind("来源")
    if pos < 0:
        return []
    out = []
    for m in _CITE_RE.finditer(answer[pos:]):
        n = int(m.group(1))
        if 1 <= n <= max_index and n not in out:
            out.append(n)
    return out


def ask(query, k=8, stream_cb=None, history=None):
    """
    知识库问答。stream_cb(token) 逐段回调；返回 {"answer", "sources", "mode",
    "rewritten_query", "cited"}。rewritten_query 是检索实际用的词（改写未发生时
    等于原 query）；cited 是答案引用编号校验过滤后的列表。
    """
    client = llm.get_chat_client()
    if not llm.chat_available():
        raise OllamaNotRunning("对话模型不可用：" + llm.chat_status()["reason"])

    # 多轮检索改写：追问（"它作者是谁"）拿原词去检索必失败，先让小模型把
    # 最近对话+新问题压成自包含检索词。清单意图直接返回文件列表，不值得为它
    # 多付一次模型调用；改写失败一律回退原 query（见 _rewrite_query）。
    rewritten = ""
    if history and not _is_list_intent(query):
        rewritten = _rewrite_query(client, query, history) or ""
    search_query = rewritten or query

    picked, mode, _qv = _retrieve(search_query, RERANK_POOL if reranker.active() else k)
    rows = _rows_in_order(picked) if picked else {}
    if reranker.active() and picked:
        picked = _apply_rerank(search_query, picked, rows, k)
    picked = [(score, rows[cid]) for cid, score in picked if cid in rows]
    # 注意：不再把 img_index（图片描述）混入 KB 问答——
    # 用户只加文本文件夹时，QQMusic 封面/应用图标等全盘图片描述会污染答案
    # 图片语义检索走独立的 search() API（Files 页"图片语义搜索"）

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
        return {"answer": answer, "sources": [], "mode": "list",
                "rewritten_query": search_query, "cited": []}

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
        return {"answer": answer, "sources": [], "mode": mode,
                "rewritten_query": search_query, "cited": []}

    # 按文件聚合：同文件的块相邻、文件之间按最高分排序——避免相关度
    # 稍低的同文件块把别的文件整体挤出上下文，也让模型按来源整段阅读
    groups, best = {}, {}
    for score, row in picked:
        p = row["file_path"]
        groups.setdefault(p, []).append((score, row))
        best[p] = max(best.get(p, 0.0), score)
    ordered = [item for p in sorted(groups, key=lambda x: -best[x])
               for item in sorted(groups[p], key=lambda x: -x[0])]

    ctx_parts = []
    for i, (score, row) in enumerate(ordered, 1):
        snippet = row["text"][:1200]
        ctx_parts.append(f"[{i}] (来源: {row['file_path']})\n{snippet}")
        title, body = _split_prefix(row["text"])
        sources.append({
            "index": i,
            "file": row["file_path"],
            "score": round(score, 4),
            "title": title,
            "locator": _locator(row["text"]),
            "snippet": body[:200],
        })

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:  # 保留最近 3 轮对话
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": QA_TEMPLATE.format(
        context="\n\n".join(ctx_parts), question=query)})

    # num_predict 封顶：RAG 答案几百字足够，防模型跑飞时无界生成拖死会话
    # （封顶切断比无限等更可接受；流式下用户早看到了前面的内容）
    answer = client.chat(messages, stream_cb=stream_cb,
                         options={"temperature": 0.3, "num_predict": 1024})
    return {"answer": answer, "sources": sources, "mode": mode,
            "rewritten_query": search_query,
            "cited": _parse_citations(answer, len(sources))}


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
