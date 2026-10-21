"""知识库失败文件可见性测试：n_failed 计数与 list_failed 查询。"""
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_kbfail_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                          # noqa: E402
from backend.api import Api                     # noqa: E402
from backend.knowledge import indexer           # noqa: E402

PASS, FAIL = 0, 0


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
    root = tempfile.mkdtemp(prefix="fb_kb_")
    fa = indexer.add_folder(root)
    fb = indexer.add_folder(os.path.join(root, "other"))
    conn = db.get_conn()
    rows = [
        (fa["id"], os.path.join(root, "ok1.pdf"), "ok"),
        (fa["id"], os.path.join(root, "broken.pdf"), "parse_failed"),
        (fb["id"], os.path.join(root, "other", "broken2.docx"), "parse_failed"),
        (fb["id"], os.path.join(root, "other", "ok2.txt"), "ok"),
    ]
    for fid, path, status in rows:
        conn.execute(
            "INSERT INTO kb_files(folder_id,path,mtime,size,hash,status) "
            "VALUES(?,?,?,?,?,?)", (fid, path, 1.0, 10, "h", status))
    conn.commit()

    folders = {f["id"]: f for f in indexer.list_folders()}
    check("n_failed counted", folders[fa["id"]]["n_failed"] == 1, str(folders[fa["id"]]))
    check("ok files not counted", folders[fb["id"]]["n_failed"] == 1, str(folders[fb["id"]]))
    check("n_files still total", folders[fa["id"]]["n_files"] == 2, str(folders[fa["id"]]))

    all_bad = indexer.list_failed()
    check("list_failed returns all", len(all_bad) == 2, str(all_bad))
    only_a = indexer.list_failed(fa["id"])
    check("list_failed filters by folder",
          len(only_a) == 1 and only_a[0]["path"].endswith("broken.pdf"), str(only_a))
    check("list_failed has mtime", "mtime" in (only_a[0] if only_a else {}), str(only_a))

    api = Api()
    r = api.kb_failed_files()
    check("api returns items", r.get("ok") is True and len(r["items"]) == 2, str(r)[:120])
    r = api.kb_failed_files(fa["id"])
    check("api filters", len(r["items"]) == 1, str(r)[:120])

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
