"""默认排除规则预置测试：空表播种 / 扫描器按默认规则跳过 / 已有规则不被追加。"""
import os
import shutil
import sys
import tempfile
import time

# 必须在导入 backend 前隔离 APPDATA：本套会写 exclude_rules / watch_roots /
# settings，直跑会污染真实用户库（退出还原也不该碰它）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_excludedef_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex
from backend.core import scanner

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _rules():
    """exclude_rules 全表现有规则（normcase 后的集合）。"""
    conn = db.get_conn()
    try:
        return {os.path.normcase(r["path"])
                for r in conn.execute("SELECT path FROM exclude_rules").fetchall()}
    finally:
        conn.close()


def main():
    global PASS, FAIL
    db.init_db()

    # (a) 空表首次 ensure_default_roots → 默认规则按 _default_exclude_rules 入库
    expect = {os.path.normcase(p) for p in
              fileindex._default_exclude_rules(fileindex.enumerate_drives())}
    check("expected set covers C:\\Windows",
          os.path.normcase(r"C:\Windows") in expect, str(sorted(expect)))
    fileindex.ensure_default_roots()
    rules = _rules()
    check("defaults seeded on empty table", expect <= rules,
          f"missing={sorted(expect - rules)}")
    check("seeded set exact", rules == expect, f"extra={sorted(rules - expect)}")
    check("seed flag recorded",
          db.get_setting("default_excludes_seeded", "") == "1")
    # 与手动 add_exclude 同形态：都是绝对路径（设置页 list_excludes/remove_exclude
    # 操作的是同一张表，绝对路径即手动规则的形态）
    check("rules are absolute paths", all(os.path.isabs(p) for p in rules),
          str(rules))

    # (c) 扫描器按默认规则跳过伪造的 node_modules 路径 + 前缀规则在两个匹配层生效
    #     （趁默认规则还在表里的时机做，(b) 会把它们清掉）
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".test_tmp")
    os.makedirs(base, exist_ok=True)
    root = tempfile.mkdtemp(prefix="fb_excl_", dir=base)
    try:
        proj = os.path.join(root, "proj", "node_modules", "somepkg")
        os.makedirs(proj)
        with open(os.path.join(proj, "index.js"), "wb") as f:
            f.write(b"console.log(1)")
        keep = os.path.join(root, "keep.txt")
        with open(keep, "wb") as f:
            f.write(b"keep")

        found = {os.path.normcase(fi["path"])
                 for fi in scanner.iter_scan_folder(root)}
        check("node_modules skipped by scanner",
              not any("node_modules" in p for p in found), str(found))
        check("normal file still indexed",
              os.path.normcase(keep) in found, str(found))

        # 预置的 C:\Windows 前缀规则在扫描匹配器上命中
        excludes = scanner._load_excludes()
        check("preset C:\\Windows rule matches",
              scanner._path_excluded(os.path.normcase(r"C:\Windows\Temp\x.txt"),
                                     excludes))
        check("non-excluded path passes",
              not scanner._path_excluded(os.path.normcase(root), excludes))
        # watcher 增量入库层（fileindex._under_skip_dir）同样命中
        check("_under_skip_dir honors preset rule",
              fileindex._under_skip_dir(r"C:\Windows\System32\drivers\etc\hosts"))
        check("_under_skip_dir honors node_modules",
              fileindex._under_skip_dir(os.path.join(root, "proj", "node_modules",
                                                     "somepkg", "index.js")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # (b) 已有规则的库（老用户）不被追加：清空后只留一条手工规则、清掉播种标记，
    #     再调 ensure_default_roots，规则表必须原样
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM exclude_rules")
        conn.execute("INSERT INTO exclude_rules(path,added_at) VALUES(?,?)",
                     (os.path.abspath(_SANDBOX), time.time()))
        conn.execute("DELETE FROM settings WHERE key='default_excludes_seeded'")
        conn.commit()
    finally:
        conn.close()
    before = _rules()
    fileindex.ensure_default_roots()
    after = _rules()
    check("existing rules untouched", after == before, f"{before} -> {after}")
    check("no default injected when table non-empty",
          os.path.normcase(r"C:\Windows") not in after, str(after))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)
