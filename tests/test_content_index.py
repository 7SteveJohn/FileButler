"""全盘内容索引（backend/contentindex.py）回归测试。

运行方式（APPDATA 必须指向一次性目录）：
    APPDATA='C:\\tmp\\fb_captest' python tests/test_content_index.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import contentindex, db, fileindex  # noqa: E402

PASS = 0
FAIL = 0
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".test_tmp", "fb_content")
PHRASE = "季度目标完成智能分类引擎"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _make_docs(n=6):
    """真文件落盘（正文级搜索必须解析真实内容）。"""
    os.makedirs(BASE, exist_ok=True)
    paths = []
    for i in range(n):
        p = os.path.join(BASE, f"doc_{i}.md" if i % 2 else f"doc_{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# 文档 {i}\n\n本文件写明{PHRASE}，第 {i} 遍。\n" + ("补充说明。" * 60) + "\n")
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
            [(p, os.path.basename(p), os.path.splitext(p)[1].lstrip(".").lower(),
              "文档", os.path.getsize(p), now) for p in paths])
        conn.commit()
    finally:
        conn.close()


def _wait(timeout=60):
    t0 = time.time()
    while contentindex.is_running() and time.time() - t0 < timeout:
        time.sleep(0.2)
    return not contentindex.is_running()


def main():
    print(f"APPDATA = {os.environ.get('APPDATA')}")
    if "fb_captest" not in (os.environ.get("APPDATA") or ""):
        print("\n拒绝执行：APPDATA 未指向一次性目录，会污染真实用户库。")
        return 2
    db.init_db()
    contentindex.ensure()
    paths = _make_docs()
    _index_rows(paths)

    print("\n[1] 首轮索引")
    pl = contentindex.plan()
    check("候选数等于文档数", pl["candidates"] == len(paths), str(pl))
    check("预算内可全部吃下", pl["will_index"] == len(paths), str(pl["will_index"]))
    r = contentindex.start()
    check("启动成功", r.get("ok") is True, str(r))
    check("跑完不超时", _wait())
    st = contentindex.status()
    check("成功篇数正确", st["ok"] == len(paths), str(st))
    check("无失败", st["failed"] == 0, str(st))
    check("统计已并入 status", st["indexed"]["ok"] == len(paths), str(st["indexed"]))

    print("\n[2] 正文命中")
    hit = contentindex.search(PHRASE, limit=20)
    check("命中全部文档", len(hit["results"]) == len(paths), str(hit)[:200])
    check("片段带命中位置", PHRASE in (hit["results"][0]["text"] or "").replace("「", "").replace("」", ""),
          repr(hit["results"][0]["text"]))
    check("结果标注 keyword 来源", hit["results"][0]["match"] == "keyword")
    check("路径可回查", hit["results"][0]["file_path"] in paths, hit["results"][0]["file_path"])
    one = contentindex.search("第 3 遍", limit=20)
    check("多词按 AND 组合", len(one["results"]) == 1 and "doc_3" in one["results"][0]["file_path"],
          str(one))
    short = contentindex.search("引擎")
    check("短查询说明原因", short["results"] == [] and "3 个字符" in short.get("reason", ""), str(short))

    print("\n[3] 增量与重跑")
    pl2 = contentindex.plan()
    check("已索引的不再进候选", pl2["candidates"] == 0, str(pl2))
    check("重跑启动后无事可做", contentindex.start().get("ok") is True and _wait()
          and contentindex.status()["ok"] == 0, str(contentindex.status()))
    with open(paths[0], "a", encoding="utf-8") as f:
        f.write("\n新追加的一段正文：归档口径以本篇为准。\n")
    os.utime(paths[0], (time.time(), time.time()))
    conn = db.get_conn()
    try:
        conn.execute("UPDATE file_index SET size=?, mtime=? WHERE path=?",
                     (os.path.getsize(paths[0]), time.time(), paths[0]))
        conn.commit()
    finally:
        conn.close()
    check("内容变化后重新进候选", contentindex.plan()["candidates"] == 1,
          str(contentindex.plan()))
    contentindex.start()
    _wait()
    again = contentindex.search("归档口径以本篇为准", limit=5)
    check("追加正文可搜到", len(again["results"]) == 1, str(again))

    print("\n[4] 预算与容错")
    # 清空正文索引让候选重新变满，否则无从验证预算截断
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM doc_content_state")
        conn.execute("DELETE FROM doc_content_fts")
        conn.commit()
    finally:
        conn.close()
    contentindex.set_budgets(max_docs=2)
    pl3 = contentindex.plan()
    check("预算截断 will_index", pl3["will_index"] == 2 and pl3["candidates"] >= 3, str(pl3))
    contentindex.start()
    _wait()
    fin3 = contentindex.status()
    check("跑完标记触顶", fin3["budget_hit"] is True, str(fin3))
    check("只索引到预算篇数", fin3["indexed"]["ok"] == 2, str(fin3["indexed"]))
    contentindex.set_budgets(max_docs=contentindex.DEF_MAX_DOCS)
    # 解析不了的文件（伪装成 pdf 的纯文本）应记为失败而不是中断整轮
    bogus = os.path.join(BASE, "bogus.pdf")
    with open(bogus, "w", encoding="utf-8") as f:
        f.write("这不是一个真的 pdf")
    conn = db.get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO file_index(path,name,ext,category,size,mtime) "
                     "VALUES(?,?,?,?,?,?)",
                     (bogus, "bogus.pdf", "pdf", "文档", os.path.getsize(bogus), time.time()))
        conn.commit()
    finally:
        conn.close()
    contentindex.start()
    _wait()
    fin = contentindex.status()
    check("坏文件不中断整轮", fin["stage"] != "error", str(fin))
    check("坏文件计入失败数", fin["failed"] >= 1, str(fin))
    check("失败文件不影响其它命中",
          len(contentindex.search(PHRASE, limit=20)["results"]) >= 1)

    print("\n[5] 清空与重建")
    before = contentindex.status()["indexed"]
    check("清空前有内容", before["ok"] >= 1, str(before))
    check("清空成功", contentindex.reset().get("ok") is True)
    after = contentindex.status()["indexed"]
    check("清空后计数归零", after["ok"] == 0 and after["docs"] == 0, str(after))
    check("清空后搜不到正文", contentindex.search(PHRASE, limit=5)["results"] == [])
    pl5 = contentindex.plan()
    check("预算额度被释放", pl5["budget"]["docs_left"] == contentindex.DEF_MAX_DOCS,
          str(pl5["budget"]))
    check("候选重新变满", pl5["candidates"] >= 6, str(pl5))
    contentindex.start()
    _wait()
    check("重建后又能搜到",
          len(contentindex.search(PHRASE, limit=20)["results"]) >= 1)

    # 清理
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index")
        conn.execute("DELETE FROM doc_content_state")
        conn.execute("DELETE FROM doc_content_fts")
        conn.commit()
    finally:
        conn.close()
    import shutil
    shutil.rmtree(os.path.join(os.path.dirname(BASE)), ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
