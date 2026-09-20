"""本 session 三项改动的回归测试：FTS 计数封顶、收藏备注、OCR 状态契约。

运行方式（APPDATA 必须指向一次性目录，否则会写进真实用户库）：
    APPDATA='C:\\tmp\\fb_captest' python tests/test_session_followups.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex  # noqa: E402
from backend.api import Api  # noqa: E402

PASS = 0
FAIL = 0
CAP = fileindex._FTS_COUNT_CAP
ALPHA = "zetaalpha"   # 命中全部造数行的公共子串
BETA = "betaterm"     # 只命中少数行


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _seed():
    """造 CAP+200 行公共子串命中，另造 3 行少数命中。"""
    db.init_db()
    fileindex.ensure_default_roots()   # 建 file_index 并补建 FTS 触发器
    db.ensure_fts()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index")
        now = time.time()
        rows = []
        for i in range(CAP + 200):
            p = f"seed://{i:06d}-{ALPHA}.doc"
            rows.append((p, f"{i:06d}-{ALPHA}.doc", "doc", "文档", 1000 + i, now - i))
        for i in range(3):
            p = f"seed://few-{i:03d}-{BETA}.txt"
            rows.append((p, f"few-{i:03d}-{BETA}.txt", "txt", "文档", 10, now - i))
        conn.executemany(
            "INSERT INTO file_index(path,name,ext,category,size,mtime) "
            "VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()


def _exact_join_count(phrase):
    """不走封顶的原始计数，用来核对封顶值没有少报。"""
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) c FROM file_index "
            "JOIN file_fts ON file_fts.rowid = file_index.id AND file_fts MATCH ?",
            (phrase,)).fetchone()["c"]
    finally:
        conn.close()


def test_capped_count():
    print("\n[1] FTS 计数封顶")
    check("FTS 可用", fileindex._fts_available())
    exact = _exact_join_count('"' + ALPHA + '"')
    check("造数确实超过封顶线", exact == CAP + 200, f"exact={exact}")

    r = fileindex.search(query=ALPHA, limit=5, sort_by="mtime")
    check("纯文本搜索计数封顶", r["total"] == CAP, f"total={r['total']}")
    check("封顶时置位 total_capped", r["total_capped"] is True)
    check("封顶不少于实际值", r["total"] <= exact)
    check("命中行仍正常返回", len(r["items"]) == 5)

    r2 = fileindex.search(query=BETA, limit=50, sort_by="mtime")
    check("未过封顶线则报实际数", r2["total"] == 3, f"total={r2['total']}")
    check("未封顶不置位", r2["total_capped"] is False)

    # 带其它过滤条件时必须走原始计数路径（封顶查询无法表达这些条件）
    r3 = fileindex.search(query=f"ext:doc {ALPHA}", limit=5)
    check("带语法过滤不封顶", r3["total_capped"] is False and r3["total"] == CAP + 200,
          f"total={r3['total']} capped={r3['total_capped']}")
    r4 = fileindex.search(query=f"category:文档 {ALPHA}", limit=5)
    check("类别过滤走原始计数", r4["total_capped"] is False,
          f"capped={r4['total_capped']}")
    r5 = fileindex.search(query=ALPHA, category="文档", limit=5)
    check("API 层 category 参数同样走原始计数", r5["total_capped"] is False
          and r5["total"] == CAP + 200, f"total={r5['total']}")
    # 无 FTS JOIN 的路径（短词 LIKE）不受影响
    r6 = fileindex.search(query="al", limit=5)
    check("短词 LIKE 路径不封顶", r6["total_capped"] is False, f"total={r6['total']}")
    r7 = fileindex.search(limit=10)
    check("空查询走原始计数", r7["total_capped"] is False, f"total={r7['total']}")


def test_favorites_note():
    print("\n[2] 收藏夹备注")
    api = Api.__new__(Api)  # 只用到 db，跳过需要窗口/缩略图服务的 __init__
    p = "seed://fav-target.doc"
    # 三个方法都会 abspath 规范化，比对统一用规范化后的键
    key = os.path.abspath(p)
    api.remove_favorite(p)
    api.add_favorite(p)
    items = api.list_favorites()["items"]
    hit = [x for x in items if x["path"] == key]
    check("收藏后可列出", len(hit) == 1, repr(items[:2]))
    check("新收藏备注为空串", (hit[0]["note"] or "") == "", repr(hit[0]["note"]))

    r = api.set_favorite_note(p, "每月 15 号前提交")
    check("写备注成功", r.get("ok") is True, str(r))
    got = [x for x in api.list_favorites()["items"] if x["path"] == key]
    check("备注已持久化", got and got[0]["note"] == "每月 15 号前提交", repr(got))

    r = api.set_favorite_note(p, "  ")
    check("空白备注按空串保存", r.get("ok") is True)
    got = [x for x in api.list_favorites()["items"] if x["path"] == key]
    check("清除备注生效", got and (got[0]["note"] or "") == "", repr(got))

    r = api.set_favorite_note("seed://never-faved.doc", "越界写入")
    check("未收藏路径拒绝写备注", r.get("ok") is False and "未在收藏夹" in r.get("error", ""),
          str(r))
    api.remove_favorite(p)
    check("移除后不再出现",
          not [x for x in api.list_favorites()["items"] if x["path"] == key])


def test_ocr_status_contract():
    print("\n[3] OCR 状态契约")
    api = Api.__new__(Api)
    st = api.ocr_status()
    check("返回 available 布尔", isinstance(st.get("available"), bool), str(st))
    check("不可用时给出原因", st["available"] or bool(st.get("reason")), str(st))
    check("键齐全", set(("available", "reason", "hint")) <= set(st), str(st))
    # 探测结果应缓存：第二次调用不再走 WinRT 互操作
    t0 = time.perf_counter()
    api.ocr_status()
    dt = time.perf_counter() - t0
    check("二次调用走缓存", dt < 0.02, f"{dt * 1000:.1f}ms")


def test_roots_sticky():
    print("\n[4] 盘根只在首次/新增时补入")
    drives = fileindex.enumerate_drives()
    if not drives:
        check("枚举到本地盘", False, "enumerate_drives 返回空，无法验证")
        return
    paths = lambda: {r["path"] for r in fileindex.list_roots(only_enabled=False)}
    check("首次运行补入全部盘根", all(d in paths() for d in drives), str(sorted(paths())))

    victim = drives[-1]
    rid = next(r["id"] for r in fileindex.list_roots(False) if r["path"] == victim)
    fileindex.remove_root(rid, delete_index=False)
    fileindex.ensure_default_roots()
    check("用户删掉的盘根不会被补回", victim not in paths(), f"{victim} 又出现了")
    check("删盘根没牵连索引行",
          db.get_conn().execute("SELECT COUNT(*) c FROM file_index").fetchone()["c"] > 5000)

    # 模拟「以前没见过的新盘」：把它从 known_drives 里抹掉，应当自动补入
    known = {x for x in (db.get_setting("known_drives", "") or "").split("|") if x}
    known.discard(victim)
    db.set_setting("known_drives", "|".join(sorted(known)))
    fileindex.ensure_default_roots()
    check("没见过的盘仍会自动补入", victim in paths(), f"{victim} 没补回来")


def main():
    print(f"APPDATA = {os.environ.get('APPDATA')}")
    print(f"DB_PATH = {db.DB_PATH}")
    if "fb_captest" not in (os.environ.get("APPDATA") or ""):
        print("\n拒绝执行：APPDATA 未指向一次性目录，会污染真实用户库。")
        return 2
    _seed()
    test_capped_count()
    test_favorites_note()
    test_ocr_status_contract()
    test_roots_sticky()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index")
        conn.execute("DELETE FROM favorites")
        conn.commit()
    finally:
        conn.close()
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
