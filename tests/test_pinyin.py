"""拼音搜索 + EXIF 拍摄时间测试（不需要 Ollama）。

覆盖：
  1. 全拼检索（baogao → 项目报告）、≥3 字首字母走 FTS（xmbg）
  2. 两字首字母 LIKE 通道（bg）、中文检索行为不变
  3. pinyin/ini 列同步进 file_fts（触发器路径）
  4. photo_time 过滤：有拍摄时间的照片按拍摄时间（dm:/mtime 区间），
     无拍摄时间/非图片回退修改时间
  5. _pinyin_pair 纯函数：中英混排、pypinyin 缺失降级

运行：.venv/Scripts/python tests/test_pinyin.py
"""
import os
import shutil
import sys
import tempfile
import time

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_pinyin_")
os.environ["APPDATA"] = _SANDBOX

# 测试夹具必须在 SKIP_DIRS 之外（AppData 下的路径会被索引跳过）
_FIXTURES = tempfile.mkdtemp(prefix="fb_pinyin_", dir=r"C:\tmp")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex  # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def _mk(name, content=b"hello"):
    p = os.path.join(_FIXTURES, name)
    with open(p, "wb") as f:
        f.write(content)
    return p


def _hits(query):
    r = fileindex.search(query=query, limit=10)
    return [i["name"] for i in r["items"]]


def test_pinyin_pair():
    print("\n[1] _pinyin_pair 纯函数")
    full, ini = fileindex._pinyin_pair("项目报告2024v2")
    check("全拼（非 CJK 原样小写）", full == "xiangmubaogao2024v2", full)
    check("首字母缩写", ini == "xmbg2024v2", ini)
    full2, ini2 = fileindex._pinyin_pair("Q3财报-final.xlsx")
    check("英数混排", full2 == "q3caibao-final.xlsx" and ini2 == "q3cb-final.xlsx",
          f"{full2} / {ini2}")


def test_pinyin_search():
    print("\n[2] 拼音检索三通道")
    paths = {name: _mk(name) for name in
             ("项目报告2024.txt", "会议纪要.txt", "readme.txt")}
    for p in paths.values():
        check(f"upsert {os.path.basename(p)}", fileindex.upsert_file(p) is not None)

    hits = _hits("baogao")
    check("全拼命中（FTS pinyin 列）", hits == ["项目报告2024.txt"], str(hits))
    hits = _hits("xmbg")
    check("4 字首字母命中（FTS pinyin_ini 列）", hits == ["项目报告2024.txt"], str(hits))
    hits = _hits("bg")
    check("2 字首字母命中（LIKE 通道）", "项目报告2024.txt" in hits, str(hits))
    hits = _hits("jiyao")
    check("另一文件全拼", hits == ["会议纪要.txt"], str(hits))
    hits = _hits("项目")
    check("中文检索行为不变", hits == ["项目报告2024.txt"], str(hits))
    hits = _hits("readme")
    check("英文原名照常", hits == ["readme.txt"], str(hits))

    # 拼音列经触发器同步进 file_fts（fi_au 路径）
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT pinyin, pinyin_ini FROM file_fts WHERE name='项目报告2024.txt'"
        ).fetchone()
        check("file_fts 同步拼音列",
              row and row["pinyin"] == "xiangmubaogao2024.txt"
              and row["pinyin_ini"] == "xmbg2024.txt", str(dict(row) if row else None))
    finally:
        conn.close()


def test_photo_time_filter():
    print("\n[3] 拍摄时间过滤（photo_time 优先，其余回退 mtime）")
    conn = db.get_conn()
    try:
        # 照片：拍摄时间设在 200 天前，修改时间（mtime）就在刚才 → dm:本周 不该命中它
        photo = _mk("beach_sunset.jpg")
        fileindex.upsert_file(photo)
        conn.execute("UPDATE file_index SET photo_time=? WHERE name='beach_sunset.jpg'",
                     (time.time() - 200 * 86400,))
        # 普通文档：mtime 刚才 → dm:本周 应命中
        doc = _mk("today_notes.txt")
        fileindex.upsert_file(doc)
        # 另一张照片：无拍摄时间（photo_time=0）→ 回退 mtime，dm:本周 命中
        fresh = _mk("fresh_photo.jpg")
        fileindex.upsert_file(fresh)
        conn.execute("UPDATE file_index SET photo_time=0 WHERE name='fresh_photo.jpg'")
        conn.commit()
    finally:
        conn.close()

    week = _hits("dm:week")
    check("照片按拍摄时间过滤（200 天前的照片不进本周）",
          "beach_sunset.jpg" not in week, str(week))
    check("无拍摄时间照片回退 mtime", "fresh_photo.jpg" in week, str(week))
    check("普通文档按 mtime", "today_notes.txt" in week, str(week))

    # mtime 区间过滤同样走 COALESCE
    now = time.time()
    r = fileindex.search(query=None, mtime_from=now - 7 * 86400, mtime_to=now + 60,
                         limit=10)
    names = [i["name"] for i in r["items"]]
    check("mtime 区间同样拍摄时间优先", "beach_sunset.jpg" not in names, str(names))

    # browse 结果暴露 photo_time（前端时间线后续可用）
    r = fileindex.search(query="beach", limit=5)
    check("结果行带 photo_time 字段",
          r["items"] and "photo_time" in r["items"][0], str(r["items"][:1]))


def test_pypinyin_missing_degrade():
    print("\n[4] pypinyin 缺失降级（不报错，写入空标记）")
    orig = sys.modules.get("pypinyin")
    sys.modules["pypinyin"] = None  # 伪装 ImportError
    try:
        full, ini = fileindex._pinyin_pair("任何名字")
        check("缺失时返回空串", full == "" and ini == "", f"{full}/{ini}")
    finally:
        if orig is None:
            sys.modules.pop("pypinyin", None)
        else:
            sys.modules["pypinyin"] = orig


def main():
    print(f"APPDATA = {os.environ.get('APPDATA')}")
    if "fb_captest" not in (os.environ.get("APPDATA") or ""):
        print("\n拒绝执行：APPDATA 未指向一次性目录。")
        return 2
    db.init_db()
    try:
        fileindex.ensure_default_roots()  # 建 file_index + FTS（含列迁移）
        test_pinyin_pair()
        test_pinyin_search()
        test_photo_time_filter()
        test_pypinyin_missing_degrade()
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)
        shutil.rmtree(_FIXTURES, ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
