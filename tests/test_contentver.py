"""全盘正文索引 parse_ver 对齐测试（backend/contentindex.py）。

覆盖：
  1. ensure() 迁移：无 parse_ver 列的旧 doc_content_state 自动补列
  2. 老格式状态行（parse_ver 为 NULL）被选为候选
  3. 索引后状态行写入当前 parse_ver 且不再进候选
  4. mtime/size 都没变但 parse_ver 不同仍会重抽

解析用真实小 .txt（参照 test_content_index 的做法，不碰模型/OCR，无 Ollama 依赖）。
运行：.venv/Scripts/python tests/test_contentver.py
"""
import os
import shutil
import sys
import tempfile
import time

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_contentver_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import contentindex, db, fileindex  # noqa: E402
from backend.knowledge import parsers  # noqa: E402

PASS = 0
FAIL = 0
BASE = os.path.join(_SANDBOX, "docs")
PHRASE = "季度目标完成智能分类引擎"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _make_docs(n=2):
    """真文件落盘（正文级搜索必须解析真实内容）。"""
    os.makedirs(BASE, exist_ok=True)
    paths = []
    for i in range(n):
        p = os.path.join(BASE, f"doc_{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# 文档 {i}\n\n本文件写明{PHRASE}，第 {i} 遍。\n")
        paths.append(p)
    return paths


def _index_rows(paths):
    fileindex.ensure_default_roots()
    db.ensure_fts()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index")
        conn.execute("DELETE FROM doc_content_state")
        conn.execute("DELETE FROM doc_content_fts")
        now = time.time()
        conn.executemany(
            "INSERT INTO file_index(path,name,ext,category,size,mtime) VALUES(?,?,?,?,?,?)",
            [(p, os.path.basename(p), "txt", "文档", os.path.getsize(p), now)
             for p in paths])
        conn.commit()
    finally:
        conn.close()


def _state_row(path):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT * FROM doc_content_state WHERE path=?", (path,)).fetchone()
    finally:
        conn.close()


def _candidates():
    conn = db.get_conn()
    try:
        return [r[0] for r in contentindex._candidates(conn)]
    finally:
        conn.close()


def _wait(timeout=60):
    t0 = time.time()
    while contentindex.is_running() and time.time() - t0 < timeout:
        time.sleep(0.2)
    return not contentindex.is_running()


def test_migration():
    """模拟旧库：按 v1.2 的表结构手工重建 doc_content_state（无 parse_ver 列）。"""
    conn = db.get_conn()
    try:
        conn.execute("DROP TABLE doc_content_state")
        conn.execute(
            "CREATE TABLE doc_content_state ("
            "path TEXT PRIMARY KEY, size INTEGER, mtime REAL, chars INTEGER, "
            "bytes INTEGER, err TEXT, fts_rowid INTEGER, indexed_at REAL)")
        conn.commit()
    finally:
        conn.close()
    contentindex._ensured = False  # 让 ensure() 重新走一遍建表 + 迁移
    contentindex.ensure()
    conn = db.get_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(doc_content_state)")}
        check("旧表补出 parse_ver 列", "parse_ver" in cols, str(sorted(cols)))
    finally:
        conn.close()


def main():
    db.init_db()
    contentindex.ensure()
    test_migration()

    paths = _make_docs()
    _index_rows(paths)

    print("\n[1] 首轮索引写入当前 parse_ver")
    pl = contentindex.plan()
    check("候选数等于文档数", pl["candidates"] == len(paths), str(pl))
    contentindex.start()
    check("跑完不超时", _wait())
    row = _state_row(paths[0])
    check("状态行写入当前 parse_ver", row["parse_ver"] == parsers.PARSE_VER,
          f"{row['parse_ver']!r} != {parsers.PARSE_VER!r}")
    pl = contentindex.plan()
    check("当前版本不再进候选", pl["candidates"] == 0, str(pl))

    print("\n[2] 老格式状态行（parse_ver 空）重抽")
    # 只降级 parse_ver，mtime/size/fts_rowid 都不动 —— 模拟 v1.2 记账的存量行
    conn = db.get_conn()
    try:
        conn.execute("UPDATE doc_content_state SET parse_ver=NULL WHERE path=?",
                     (paths[0],))
        conn.commit()
    finally:
        conn.close()
    old_rowid = _state_row(paths[0])["fts_rowid"]
    cand = _candidates()
    check("老格式行被选为候选", cand == [paths[0]], str(cand))
    check("plan 计数同步", contentindex.plan()["candidates"] == 1,
          str(contentindex.plan()))
    contentindex.start()
    check("重抽跑完不超时", _wait())
    row = _state_row(paths[0])
    check("重抽后写入当前 parse_ver", row["parse_ver"] == parsers.PARSE_VER,
          f"{row['parse_ver']!r}")
    check("旧 FTS 行被替换", row["fts_rowid"] != old_rowid,
          f"{row['fts_rowid']} == {old_rowid}")
    check("重抽后候选清零", contentindex.plan()["candidates"] == 0,
          str(contentindex.plan()))

    print("\n[3] mtime/size 不变、parse_ver 不同仍重抽")
    conn = db.get_conn()
    try:
        conn.execute("UPDATE doc_content_state SET parse_ver='1' WHERE path=?",
                     (paths[1],))
        conn.commit()
    finally:
        conn.close()
    cand = _candidates()
    check("版本不同的行进候选", cand == [paths[1]], str(cand))
    check("mtime/size 确实没变",
          _state_row(paths[1])["size"] == os.path.getsize(paths[1]))
    contentindex.start()
    check("重抽跑完不超时", _wait())
    row = _state_row(paths[1])
    check("重抽后归位当前 parse_ver", row["parse_ver"] == parsers.PARSE_VER,
          f"{row['parse_ver']!r}")
    check("候选清零", contentindex.plan()["candidates"] == 0,
          str(contentindex.plan()))
    hit = contentindex.search(PHRASE, limit=5)
    check("重抽后正文仍可搜", len(hit["results"]) == 2, str(hit)[:200])

    # 清理
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index")
        conn.execute("DELETE FROM doc_content_state")
        conn.execute("DELETE FROM doc_content_fts")
        conn.commit()
    finally:
        conn.close()
    db.close_all_conns()
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
