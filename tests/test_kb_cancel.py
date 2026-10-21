"""知识库索引取消测试：cancel 置位后立即停，已处理数量如实返回。"""
import os
import sys
import tempfile
import threading

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_kbcancel_")
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
    root = tempfile.mkdtemp(prefix="fb_kbc_")
    for n in ("a.txt", "b.txt", "c.txt"):
        with open(os.path.join(root, n), "w", encoding="utf-8") as f:
            f.write("content " + n)
    folder = indexer.add_folder(root)

    # 打桩：不跑真实嵌入，只验证索引循环与取消语义
    indexer.llm.embed_available = lambda: True
    indexer.llm.get_embed_client = lambda: object()
    calls = []

    def fake_index(client, fi, fid):
        calls.append(fi["path"])
        return "ok"

    indexer._index_file_into_kb = fake_index

    # 1. 未取消：全部处理
    stages = []
    r = indexer.index_folder(folder["id"], lambda *a: stages.append(a))
    check("indexed all", r.get("indexed") == 3, str(r))
    check("not cancelled flag", not r.get("cancelled"), str(r))
    check("calls recorded", len(calls) == 3, str(calls))
    check("progress has parse stage", any(s[0] == "parse" for s in stages), str(stages)[:120])

    # 2. 已取消：一个都不处理
    calls.clear()
    ev = threading.Event()
    ev.set()
    r = indexer.index_folder(folder["id"], None, cancel=ev)
    check("cancelled before start", r.get("cancelled") is True, str(r))
    check("nothing indexed", r.get("indexed") == 0 and not calls, f"{r} {calls}")

    # 3. 处理到一半取消：只处理第一个
    calls.clear()
    ev2 = threading.Event()

    def fake_index_cancel_once(client, fi, fid):
        calls.append(fi["path"])
        ev2.set()          # 处理完第一个文件后立刻置位
        return "ok"

    indexer._index_file_into_kb = fake_index_cancel_once
    r = indexer.index_folder(folder["id"], None, cancel=ev2)
    check("stopped mid-way", r.get("cancelled") is True and r.get("indexed") == 1, str(r))
    check("only one file processed", len(calls) == 1, str(calls))

    # 4. api 层：没有在跑的索引时取消应明确报错
    api = Api()
    r = api.kb_index_cancel(99999)
    check("cancel without job errors",
          r.get("ok") is False and "没有" in str(r.get("error")), str(r))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
