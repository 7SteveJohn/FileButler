"""知识库索引：扫描文件夹 → 解析 → 切块 → 向量化 → 入库（增量更新）。"""
import os
import sqlite3
import struct

from backend import db
from backend.core import scanner
from backend.knowledge import chunker, parsers
from backend import llm
from backend.ollama_client import OllamaNotRunning

# numpy 惰性导入：仅在真正向量化时加载（省纯搜索用户的常驻内存）


def _vec_to_blob(vec):
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def add_folder(path, source="manual"):
    """添加知识库文件夹。"""
    path = os.path.abspath(path)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO kb_folders(path, added_at, source) VALUES(?,?,?)",
            (path, import_time(), source))
        conn.commit()
        row = conn.execute("SELECT id FROM kb_folders WHERE path=?", (path,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def sync_auto_folders():
    """监控根目录 ↔ auto 知识库文件夹同步：新根目录建 auto 文件夹，已删根目录移除其 auto 文件夹。
    返回 (added_paths, removed_paths)。"""
    from backend import fileindex
    roots = fileindex.list_roots()
    conn = db.get_conn()
    try:
        autos = {r["path"]: r["id"] for r in conn.execute(
            "SELECT id, path FROM kb_folders WHERE source='auto'")}
    finally:
        conn.close()
    root_paths = {r["path"] for r in roots}
    added, removed = [], []
    for p in root_paths - set(autos):
        add_folder(p, source="auto")
        added.append(p)
    for p in set(autos) - root_paths:
        remove_folder(autos[p])
        removed.append(p)
    return added, removed


def import_time():
    import time
    return time.time()


def remove_folder(folder_id):
    """移除文件夹及其全部索引数据。"""
    conn = db.get_conn()
    try:
        conn.execute(
            "DELETE FROM chunks WHERE file_id IN (SELECT id FROM kb_files WHERE folder_id=?)",
            (folder_id,))
        conn.execute("DELETE FROM kb_files WHERE folder_id=?", (folder_id,))
        conn.execute("DELETE FROM kb_folders WHERE id=?", (folder_id,))
        conn.commit()
    finally:
        conn.close()
    db.vec_index.invalidate()


def list_folders():
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT f.id, f.path, f.last_index_at, COALESCE(f.source,'manual') AS source, "
            "(SELECT COUNT(*) FROM kb_files k WHERE k.folder_id=f.id) AS n_files, "
            "(SELECT COUNT(*) FROM chunks c JOIN kb_files k ON k.id=c.file_id "
            " WHERE k.folder_id=f.id) AS n_chunks "
            "FROM kb_folders f ORDER BY f.id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def index_folder(folder_id, progress_cb=None):
    """
    增量索引一个文件夹。progress_cb(stage, i, n, detail)：
      stage: scan/parse/embed/done/clean
    返回 {"indexed": n, "skipped": n, "removed": n, "failed": n}
    """
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT path FROM kb_folders WHERE id=?", (folder_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"error": "文件夹不存在"}
    root = row["path"]
    if not os.path.isdir(root):
        return {"error": f"路径不可访问：{root}"}

    if not llm.embed_available():
        raise OllamaNotRunning("向量模型未就绪：" + llm.embed_status()["reason"])
    client = llm.get_embed_client()

    # 1. 扫描
    if progress_cb:
        progress_cb("scan", 0, 0, root)
    files = [f for f in scanner.scan_folder(root) if f["ext"] in parsers.SUPPORTED]

    # 2. 增量判断：mtime+size 未变的跳过
    conn = db.get_conn()
    try:
        known = {r["path"]: r for r in conn.execute(
            "SELECT id, path, mtime, size FROM kb_files WHERE folder_id=?", (folder_id,))}
    finally:
        conn.close()

    todo = []
    skipped = 0
    for fi in files:
        old = known.get(fi["path"])
        if old and abs(old["mtime"] - fi["mtime"]) < 1 and old["size"] == fi["size"]:
            skipped += 1
        else:
            todo.append(fi)

    indexed, failed = 0, 0
    folder_rows = None
    conn = db.get_conn()
    try:
        folder_rows = conn.execute("SELECT id, path FROM kb_folders").fetchall()
    finally:
        conn.close()
    for i, fi in enumerate(todo):
        if progress_cb:
            progress_cb("parse", i + 1, len(todo), fi["name"])
        status_one = _index_file_into_kb(client, fi, _folder_id_of(fi["path"], folder_rows))
        if status_one == "ok":
            indexed += 1
        else:
            failed += 1

    # 3. 清理已删除文件的索引
    removed = _clean_missing(folder_id)

    conn = db.get_conn()
    try:
        conn.execute("UPDATE kb_folders SET last_index_at=? WHERE id=?",
                     (import_time(), folder_id))
        conn.commit()
    finally:
        conn.close()
    db.vec_index.invalidate()
    if progress_cb:
        progress_cb("done", indexed, len(todo), "")
    return {"indexed": indexed, "skipped": skipped, "removed": removed, "failed": failed}


def _folder_id_of(path, folder_rows=None):
    """文件路径 → 所属知识库文件夹 id（最长前缀匹配，auto/manual 均可）。
    folder_rows 传入本轮缓存的 kb_folders 行，避免每个文件重查一次库。"""
    if folder_rows is None:
        conn = db.get_conn()
        try:
            folder_rows = conn.execute("SELECT id, path FROM kb_folders").fetchall()
        finally:
            conn.close()
    best_id, best_len = None, -1
    norm = os.path.normcase(os.path.abspath(path))
    for r in folder_rows:
        rn = os.path.normcase(r["path"])
        if norm.startswith(rn.rstrip("\\") + os.sep) and len(rn) > best_len:
            best_id, best_len = r["id"], len(rn)
    return best_id


def _index_file_into_kb(client, fi, folder_id):
    """解析单个文件并写入知识库。fi 含 path/mtime/size。返回 'ok'/'skip'/'failed'。
    注意：不再计算全文件 SHA256（kb_files.hash 无任何消费方，增量判断走
    mtime+size；11MB 扫描件算一遍哈希 = 白读一遍盘）。"""
    if folder_id is None:
        return "skip"
    text = parsers.parse_file(fi["path"])
    if not text:
        _mark_parse_failed(folder_id, fi)
        return "failed"
    pieces = chunker.chunk_text(text)
    if not pieces:
        return "failed"
    try:
        vectors = client.embed(pieces)
    except Exception:
        return "failed"

    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE file_id IN "
                     "(SELECT id FROM kb_files WHERE path=?)", (fi["path"],))
        conn.execute("DELETE FROM kb_files WHERE path=?", (fi["path"],))
        cur = conn.execute(
            "INSERT INTO kb_files(folder_id,path,mtime,size,hash) VALUES(?,?,?,?,?)",
            (folder_id, fi["path"], fi["mtime"], fi["size"], None))
        fid = cur.lastrowid
        rows = [(fid, seq, piece, _vec_to_blob(v))
                for seq, (piece, v) in enumerate(zip(pieces, vectors))]
        conn.executemany("INSERT INTO chunks(file_id,seq,text,vec) VALUES(?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    db.vec_index.invalidate()
    return "ok"


def _mark_parse_failed(folder_id, fi):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM kb_files WHERE path=?", (fi["path"],))
        conn.execute(
            "INSERT INTO kb_files(folder_id,path,mtime,size,hash,status) VALUES(?,?,?,?,?,?)",
            (folder_id, fi["path"], fi["mtime"], fi["size"], None, "parse_failed"))
        conn.commit()
    finally:
        conn.close()


def index_files(paths, progress_cb=None):
    """增量索引指定文件（watcher 自动队列调用）。
    跳过未变化的；返回 {"indexed": n, "failed": n, "skipped": n}。"""
    paths = [p for p in paths if os.path.splitext(p)[1].lstrip(".").lower() in parsers.SUPPORTED]
    if not paths:
        return {"indexed": 0, "failed": 0, "skipped": 0}
    if not llm.embed_available():
        return {"error": "向量模型未就绪，跳过本轮自动索引：" + llm.embed_status()["reason"]}
    client = llm.get_embed_client()

    conn = db.get_conn()
    try:
        known = {r["path"]: r for r in conn.execute("SELECT path, mtime, size FROM kb_files")}
    finally:
        conn.close()

    indexed = failed = skipped = 0
    for i, path in enumerate(paths):
        if progress_cb:
            progress_cb(i + 1, len(paths), os.path.basename(path))
        if not os.path.exists(path):
            remove_kb_file(path)
            continue
        old = known.get(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if old and abs(old["mtime"] - st.st_mtime) < 1 and old["size"] == st.st_size \
                and _folder_id_of(path) is not None:
            skipped += 1
            continue
        fi = {"path": path, "mtime": st.st_mtime, "size": st.st_size}
        r = _index_file_into_kb(client, fi, _folder_id_of(path))
        if r == "ok":
            indexed += 1
        elif r == "failed":
            failed += 1
        else:
            skipped += 1
    return {"indexed": indexed, "failed": failed, "skipped": skipped}


def remove_kb_file(path):
    """从知识库移除单个文件（文件被删除时 watcher 调用）。"""
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE file_id IN "
                     "(SELECT id FROM kb_files WHERE path=?)", (path,))
        conn.execute("DELETE FROM kb_files WHERE path=?", (path,))
        conn.commit()
    finally:
        conn.close()
    db.vec_index.invalidate()


def _upsert_file(conn, folder_id, fi, status_flag):
    _mark_parse_failed(folder_id, fi)


def _clean_missing(folder_id):
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, path FROM kb_files WHERE folder_id=?", (folder_id,)).fetchall()
        removed = 0
        for r in rows:
            if not os.path.exists(r["path"]):
                conn.execute("DELETE FROM chunks WHERE file_id=?", (r["id"],))
                conn.execute("DELETE FROM kb_files WHERE id=?", (r["id"],))
                removed += 1
        conn.commit()
        return removed
    finally:
        conn.close()
