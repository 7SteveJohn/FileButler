"""重复清理的「人工指定保留项」测试。

对比预览里用户点选的是哪一张，就必须留哪一张——不能点完还被 keep_rule 覆盖，
也不能因为指定的文件刚好不见了就退化成删错人。
"""
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_keep_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                          # noqa: E402
from backend.core import cleanup, executor      # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def make_files(root, names):
    """三份内容相同、mtime 递增的文件：a 最旧、c 最新。"""
    paths = []
    base = 1_600_000_000
    for i, n in enumerate(names):
        p = os.path.join(root, n)
        with open(p, "w", encoding="utf-8") as f:
            f.write("same content")
        os.utime(p, (base + i * 100, base + i * 100))
        paths.append(p)
    return paths


def main():
    db.init_db()
    root = tempfile.mkdtemp(prefix="fb_keep_")
    a, b, c = make_files(root, ["a.txt", "b.txt", "c.txt"])
    group = {"hash": "h1", "paths": [a, b, c]}

    # 1) 不指定：按 newest 保留 c
    r = cleanup.dedupe_cleanup([dict(group)], keep_rule="newest")
    check("默认规则保留最新", os.path.exists(c) and not os.path.exists(a)
          and not os.path.exists(b), str(r))
    check("移动计数", r["moved"] == 2 and r["kept"] == 1 and r["failed"] == 0, str(r))

    # 2) 撤销：a、b 应回来
    u = executor.undo(r["batch_id"])
    check("撤销后原文件回来", os.path.exists(a) and os.path.exists(b), str(u))

    # 3) 指定保留最旧的 a，规则仍是最新
    r = cleanup.dedupe_cleanup([dict(group)], keep_rule="newest", keep_paths=[a])
    check("人工指定优先于规则", os.path.exists(a) and not os.path.exists(b)
          and not os.path.exists(c), str(r))
    executor.undo(r["batch_id"])
    check("再次撤销恢复", os.path.exists(b) and os.path.exists(c))

    # 4) 指定一个不存在的路径：必须回退规则（保留 c），不能把三份都搬走
    r = cleanup.dedupe_cleanup([dict(group)], keep_rule="newest",
                               keep_paths=[os.path.join(root, "ghost.txt")])
    check("指定失效时回退规则", os.path.exists(c) and r["moved"] == 2, str(r))
    executor.undo(r["batch_id"])

    # 5) dry_run 不落盘
    before = sorted(os.listdir(root))
    r = cleanup.dedupe_cleanup([dict(group)], keep_rule="newest", dry_run=True)
    check("dry_run 不动文件", sorted(os.listdir(root)) == before and r["moved"] == 2, str(r))

    # 6) 每组只剩一个文件时不报错
    one = os.path.join(root, "only.txt")
    open(one, "w").write("x")
    r = cleanup.dedupe_cleanup([{"hash": "h2", "paths": [one]}])
    check("单文件组跳过", r["moved"] == 0 and r["kept"] == 0, str(r))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
