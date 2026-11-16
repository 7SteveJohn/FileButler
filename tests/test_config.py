"""配置导出/导入测试：七类数据的往返、计数、幂等与错误输入。

设置页的按钮曾经只写了模板没接函数，后端这两个方法因此长期无人调用；
这里把后端行为钉死，前端接线由 tests 之外的手工/UI 验证兜底。
"""
import json
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_config_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                          # noqa: E402
from backend.api import Api                     # noqa: E402

PASS, FAIL = 0, 0

PAYLOAD = {
    "app": "FileButler",
    "version": 1,
    "rules": [{"pattern": "*.pdf", "category": "文档", "sub": "PDF"}],
    "templates": [{"name": "按类型", "target_root": "D:/sorted", "images_by_year": 1}],
    "tags": [{"id": 7, "name": "重要"}, {"id": 8, "name": "待办"}],
    "file_tags": [{"path": "C:/a.pdf", "tag_id": 7}],
    "excludes": [{"path": "D:/node_modules"}],
    "favorites": [{"path": "C:/a.pdf", "note": "常用"}],
    "saved_searches": [{"name": "大文件", "query": "size:>100mb"}],
}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    db.init_db()
    api = Api()

    # 空库导出：结构完整，七类键都在
    r = api.export_config()
    check("空库导出 ok", r.get("ok") is True, str(r)[:120])
    doc = json.loads(r["json"])
    check("导出标识 app/version", doc["app"] == "FileButler" and doc["version"] == 1)
    for key in ("rules", "templates", "tags", "file_tags", "excludes",
                "favorites", "saved_searches"):
        check(f"导出含 {key}", isinstance(doc.get(key), list))

    # 导入：每类都要计入 counts（tags 曾经漏统计，界面会少报新增数）
    r = api.import_config(json.dumps(PAYLOAD, ensure_ascii=False))
    check("导入 ok", r.get("ok") is True, str(r)[:120])
    for key, want in (("rules", 1), ("templates", 1), ("tags", 2), ("file_tags", 1),
                      ("excludes", 1), ("favorites", 1), ("saved_searches", 1)):
        check(f"{key} 计数 = {want}", r["counts"].get(key) == want, str(r["counts"]))

    # 标签 id 重映射：源库 id=7 到本库未必还是 7，file_tags 必须跟新 id 走
    conn = db.get_conn()
    try:
        tag = conn.execute("SELECT id FROM tags WHERE name='重要'").fetchone()
        ft = conn.execute("SELECT tag_id FROM file_tags WHERE path='C:/a.pdf'").fetchone()
        check("标签 id 重映射", tag and ft and ft["tag_id"] == tag["id"],
              f"tag={dict(tag) if tag else None} ft={dict(ft) if ft else None}")
        check("收藏备注保留", conn.execute(
            "SELECT note FROM favorites WHERE path='C:/a.pdf'").fetchone()["note"] == "常用")
    finally:
        conn.close()

    # 幂等：同一 payload 再导一次，什么都不该新增
    r2 = api.import_config(json.dumps(PAYLOAD, ensure_ascii=False))
    check("重复导入全部计 0", set(r2["counts"].values()) == {0}, str(r2["counts"]))

    # 合并不覆盖：已有标签改名式的重复导入不得产生第二条同名标签
    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM tags WHERE name='重要'").fetchone()["c"]
        check("同名标签只有一条", n == 1, str(n))
    finally:
        conn.close()

    # 导出现有库 → 再导入自己，必须完全幂等（往返一致性）
    again = api.import_config(api.export_config()["json"])
    check("导出→导入自身幂等", set(again["counts"].values()) == {0}, str(again["counts"]))

    # 错误输入：不崩、不写入、返回可读错误
    check("坏 JSON 拒绝", api.import_config("{bad")["ok"] is False)
    check("非 FileButler 文件拒绝", api.import_config('{"app":"Other"}')["ok"] is False)
    # 没有 app 标识同样按「非本应用导出」拒绝，但要返回错误而不是抛异常
    r = api.import_config("{}")
    check("空对象拒绝且不抛异常", r["ok"] is False and bool(r.get("error")), str(r)[:80])
    check("缺字段条目被跳过", api.import_config(json.dumps(
        {"app": "FileButler", "rules": [{"category": "文档"}], "tags": [{"id": 1}]}
    ))["ok"] is True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
