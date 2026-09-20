"""全盘文档正文索引：把已编目文档的正文抽进独立 FTS5 表，提供内容级关键词搜索。

与「知识库」的向量检索互补：这里不依赖任何模型、不产生向量，只做 trigram 子串
命中，覆盖面是全部编目文档（PDF/Word/PPT/Excel/文本/代码）。

成本受控（见 budgets()）：单文件大小上限、单文件正文字符上限、总篇数与总字节
预算；默认**不**自动运行，由设置页手动启动，可随时停止并续跑（增量记账在
doc_content_state，按 mtime+size 判断是否需要重抽）。

表设计说明：doc_content_fts 用独立表而非外部内容表（content=''），原因见
db._ensure_fts 的注释——外部内容表 + trigram + WAL 在早期 SQLite 上 rebuild 会
损坏库；独立表把正文存一份，换来 snippet 可取、删除按 rowid O(1)。
"""
import os
import threading
import time

from backend import db
from backend.knowledge import parsers

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_content_state (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    chars INTEGER,
    bytes INTEGER,
    err TEXT,
    fts_rowid INTEGER,
    indexed_at REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_content_fts USING fts5(
    body, tokenize='trigram');
"""

# 预算默认值（可在设置里改，存 settings 表）
MAX_FILE_BYTES = 10 * 1024 * 1024      # 单文件磁盘大小上限
MAX_DOC_CHARS = 200000                  # 单文件正文截断（字符）
DEF_MAX_DOCS = 50000                    # 总篇数上限
DEF_MAX_BODY_BYTES = 2 * 1024 ** 3      # 入库正文总字节上限
_BATCH_COMMIT = 25

_lock = threading.Lock()
_stop = threading.Event()
_prog = {"running": False, "done": 0, "total": 0, "ok": 0, "failed": 0,
         "body_bytes": 0, "budget_hit": False, "stage": "", "detail": "",
         "started_at": 0.0, "finished_at": 0.0}

_ensured = False


def ensure():
    """建表（幂等，进程内只做一次）。"""
    global _ensured
    if _ensured:
        return
    conn = db.get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _ensured = True
    finally:
        conn.close()


def budgets():
    """(总篇数上限, 入库正文字节上限)，来自 settings。"""
    try:
        n = int(db.get_setting("content_max_docs", str(DEF_MAX_DOCS)))
    except (TypeError, ValueError):
        n = DEF_MAX_DOCS
    try:
        b = int(db.get_setting("content_max_body_bytes", str(DEF_MAX_BODY_BYTES)))
    except (TypeError, ValueError):
        b = DEF_MAX_BODY_BYTES
    return max(0, n), max(0, b)


def set_budgets(max_docs=None, max_body_bytes=None):
    if max_docs is not None:
        db.set_setting("content_max_docs", str(int(max_docs)))
    if max_body_bytes is not None:
        db.set_setting("content_max_body_bytes", str(int(max_body_bytes)))


def totals():
    """已索引统计（供设置页与预算判断）。"""
    ensure()
    conn = db.get_conn()
    try:
        r = conn.execute(
            "SELECT COUNT(*) n, "
            "COALESCE(SUM(CASE WHEN err IS NULL THEN 1 ELSE 0 END), 0) ok, "
            "COALESCE(SUM(CASE WHEN err IS NULL THEN 0 ELSE 1 END), 0) failed, "
            "COALESCE(SUM(bytes), 0) bytes "
            "FROM doc_content_state").fetchone()
        return {"docs": r["n"], "ok": r["ok"], "failed": r["failed"],
                "body_bytes": r["bytes"]}
    finally:
        conn.close()


def _cand_sql(select):
    """候选集 SQL 片段（受支持格式 + 体积在限内 + 没抽过或已变化）。

    plan() 只要个数、_run() 要取行，两处共用同一个谓词，避免计数与实际索引口径漂移。
    """
    exts = sorted(parsers.SUPPORTED)
    marks = ",".join("?" * len(exts))
    # 只拼占位符，不拼用户数据（同 fileindex.search 的做法）
    sql = ("SELECT " + select + " FROM file_index f "
           "LEFT JOIN doc_content_state s ON s.path = f.path "
           "WHERE f.ext IN (" + marks + ") AND f.size > 0 AND f.size <= ? "
           "AND (s.path IS NULL OR s.mtime <> f.mtime OR s.size <> f.size)")
    return sql, tuple(exts) + (MAX_FILE_BYTES,)


def _candidates(conn):
    sql, params = _cand_sql("f.path AS path, f.size AS size, f.mtime AS mtime")
    rows = conn.execute(sql + " ORDER BY f.mtime DESC", params).fetchall()
    return [(r["path"], r["size"], r["mtime"]) for r in rows]


def plan():
    """预算内还能吃多少、队列里有多少候选（不建索引，只算数）。"""
    ensure()
    max_docs, max_bytes = budgets()
    t = totals()
    conn = db.get_conn()
    try:
        sql, params = _cand_sql("COUNT(*) AS c")
        n_cand = conn.execute(sql, params).fetchone()["c"]
    finally:
        conn.close()
    docs_left = max(0, max_docs - t["ok"])
    bytes_left = max(0, max_bytes - t["body_bytes"])
    return {"candidates": n_cand, "will_index": min(n_cand, docs_left),
            "indexed": t, "budget": {"max_docs": max_docs, "max_body_bytes": max_bytes,
                                      "docs_left": docs_left, "bytes_left": bytes_left}}


def reset():
    """清空正文索引（FTS 与增量记账一起清），用于重建或回收预算额度。

    注意：删行不会让库文件变小（SQLite 只是把页放进 freelist），要真的把磁盘腾出来
    还得再跑一次设置页的「压缩数据库」（db.vacuum 会撤掉各连接的映射后截断文件）。
    """
    ensure()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM doc_content_fts")
        conn.execute("DELETE FROM doc_content_state")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def status():
    """进度快照 + 已索引统计（跑动中也能廉价刷新，plan 才需要扫候选集）。"""
    out = dict(_prog)
    out["finished_at"] = out["finished_at"] or None
    out["indexed"] = totals()
    return out


def is_running():
    return bool(_prog["running"])


def stop():
    """请求停止：跑完当前文件后退出，已提交的进度保留。"""
    _stop.set()
    return {"ok": True, "running": is_running()}


def start(progress_cb=None):
    """后台启动一轮增量索引。已在跑则直接返回当前进度。"""
    ensure()
    with _lock:
        if _prog["running"]:
            return {"ok": True, "already": True}
        _stop.clear()
        _prog.update({"running": True, "done": 0, "total": 0, "ok": 0, "failed": 0,
                      "body_bytes": 0, "budget_hit": False, "stage": "planning",
                      "detail": "", "started_at": time.time(), "finished_at": 0.0})

        def _cb(stage, i, n, detail=""):
            _prog.update({"stage": stage, "done": i, "total": n, "detail": detail})
            if progress_cb:
                try:
                    progress_cb(stage, i, n, detail)
                except Exception:
                    pass

        th = threading.Thread(target=_run, args=(_cb,), daemon=True,
                              name="fb-content-index")
        th.start()
        return {"ok": True}


def _lower_priority():
    """后台索引不该和用户抢 CPU/磁盘。"""
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(h, -1)   # THREAD_PRIORITY_LOWEST
    except Exception:
        pass


def _run(cb):
    _lower_priority()
    max_docs, max_bytes = budgets()
    t = totals()
    done = ok = failed = 0
    body_bytes = t["body_bytes"]
    docs_ok = t["ok"]
    budget_hit = False
    try:
        conn = db.get_conn()
        try:
            todo = _candidates(conn)
            n = len(todo)
            cb("indexing", 0, n, f"已索引 {docs_ok} 篇")
            batch = 0
            for path, size, mtime in todo:
                if _stop.is_set():
                    cb("stopped", done, n, "已停止")
                    break
                if docs_ok >= max_docs or body_bytes >= max_bytes:
                    budget_hit = True
                    cb("budget", done, n, "已达预算上限")
                    break
                err = None
                rowid = None
                chars = 0
                nbytes = 0
                try:
                    if not os.path.isfile(path):
                        raise OSError("文件已不存在")
                    body = parsers.parse_file(path)
                    if not body:
                        err = "无正文（扫描件或解析器不支持）"
                    else:
                        if len(body) > MAX_DOC_CHARS:
                            body = body[:MAX_DOC_CHARS]
                        chars = len(body)
                        nbytes = len(body.encode("utf-8"))
                except Exception as e:
                    err = (str(e) or e.__class__.__name__)[:160]

                old = conn.execute(
                    "SELECT fts_rowid FROM doc_content_state WHERE path=?",
                    (path,)).fetchone()
                if old and old["fts_rowid"]:
                    conn.execute("DELETE FROM doc_content_fts WHERE rowid=?",
                                 (old["fts_rowid"],))
                if err is None:
                    rowid = conn.execute(
                        "INSERT INTO doc_content_fts(body) VALUES(?)", (body,)
                    ).lastrowid
                    ok += 1
                    docs_ok += 1
                    body_bytes += nbytes
                else:
                    failed += 1
                conn.execute(
                    "INSERT OR REPLACE INTO doc_content_state"
                    "(path,size,mtime,chars,bytes,err,fts_rowid,indexed_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (path, size, mtime, chars, nbytes, err, rowid, time.time()))
                done += 1
                batch += 1
                if batch >= _BATCH_COMMIT:
                    conn.commit()
                    batch = 0
                    _prog.update({"ok": ok, "failed": failed, "body_bytes": body_bytes})
                    cb("indexing", done, n, os.path.basename(path))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _prog.update({"stage": "error", "detail": str(e)[:200]})
    finally:
        _prog.update({"running": False, "done": done, "ok": ok, "failed": failed,
                      "body_bytes": body_bytes, "budget_hit": budget_hit,
                      "finished_at": time.time()})
        cb(_prog["stage"] or "done", done, _prog["total"], "完成")


# ---------- 搜索 ----------

def _match_expr(text):
    """查询 → FTS5 表达式。

    trigram 的匹配粒度是 3 个字符，所以：
    - 有 ≥3 字符的词 → 这些词按 AND 组合（短词忽略，否则整条查询会因一个两字词而全灭）
    - 全是短词（如「第 3 遍」）→ 把整串当一个连续短语，命中概率远高于拆成单字
    - 整串也不足 3 字符 → 返回 None，由调用方给出说明
    """
    toks = [t for t in text.split() if t]
    long_toks = [t for t in toks if len(t) >= 3]
    src = long_toks if long_toks else ([text] if len(text) >= 3 else [])
    if not src:
        return None
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in src)


def search(query, limit=20):
    """正文关键词命中：返回 [{file_path, text, match}]，text 是带标记的片段。

    trigram 分词粒度是 3 个字符，短于 3 字符的查询直接返回空并说明原因
    （这种查询走文件名搜索更合适，前端已有该通道）。
    """
    ensure()
    text = (query or "").strip()
    match = _match_expr(text)
    if not match:
        return {"results": [], "reason": "正文搜索至少需要 3 个字符"}
    limit = max(1, min(int(limit or 20), 100))
    conn = db.get_conn()
    try:
        # 片段标记用中文引号：正文内容不可信，前端不能按 HTML 渲染，
        # 而 [] 容易和文件名里的方括号混淆
        rows = conn.execute(
            "SELECT s.path AS path, "
            "snippet(doc_content_fts, 0, '「', '」', ' … ', 12) AS snip "
            "FROM doc_content_fts JOIN doc_content_state s ON s.fts_rowid = doc_content_fts.rowid "
            "WHERE doc_content_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit)).fetchall()
    except Exception as e:
        return {"results": [], "reason": str(e)[:160]}
    finally:
        conn.close()
    return {"results": [{"file_path": r["path"], "text": r["snip"] or "",
                         "match": "keyword"} for r in rows]}
