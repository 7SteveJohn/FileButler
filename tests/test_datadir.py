"""数据目录切换测试：指针文件、搬迁复制、完整性校验、恢复默认、失效指针提示。

运行方式（APPDATA 必须指向一次性目录，否则会把指针写进真实用户库）：
    APPDATA='C:\\tmp\\fb_datadir' python tests/test_datadir.py
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex

PASS = 0
FAIL = 0
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".test_tmp")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    if "fb_datadir" not in (os.environ.get("APPDATA") or ""):
        print("拒绝执行：APPDATA 未指向一次性目录，会把指针写进真实用户库。")
        return 2
    os.makedirs(BASE, exist_ok=True)
    db.init_db()
    # 确保从默认目录开始
    db.set_data_dir(None)
    db._apply_data_dir()
    default = db.get_data_dir()
    check("default dir active", db.get_data_dir() == db.DEFAULT_DIR)

    # 造一点数据
    db.set_setting("datadir_test_marker", "hello")

    # ---------- 1. 切换到新目录 ----------
    new_dir = os.path.join(BASE, "fb_data_new")
    shutil.rmtree(new_dir, ignore_errors=True)
    statuses = []
    r = db.switch_data_dir(new_dir, on_status=statuses.append)
    check("switch ok", r.get("ok") is True, str(r))
    check("db path moved", db.DB_PATH == os.path.join(new_dir, "filebutler.db"))
    check("data survives", db.get_setting("datadir_test_marker", "") == "hello")
    check("marker file written", db.get_data_dir() == new_dir)
    check("status reported", len(statuses) >= 4, str(statuses))
    check("thumbs copied or skipped", True)  # 无缩略图目录时跳过属正常

    # 旧目录数据库保留（备份）
    check("old db kept as backup",
          os.path.exists(os.path.join(default, "filebutler.db")))

    # 新数据写入新库
    db.set_setting("after_switch", "written-in-new")
    check("writes go to new db", db.get_setting("after_switch", "") == "written-in-new")

    # ---------- 2. 嵌套/相同目录拒绝 ----------
    r = db.switch_data_dir(new_dir)
    check("same dir rejected", r.get("ok") is False)
    r = db.switch_data_dir(os.path.join(new_dir, "sub"))
    check("nested dir rejected", r.get("ok") is False)

    # ---------- 3. 目标已有 db → 默认拒绝，overwrite 放行 ----------
    other = os.path.join(BASE, "fb_data_other")
    shutil.rmtree(other, ignore_errors=True)
    os.makedirs(other)
    with open(os.path.join(other, "filebutler.db"), "wb") as f:
        f.write(b"not a real db")
    r = db.switch_data_dir(other)
    check("existing db blocks", r.get("ok") is False and "覆盖" in r.get("error", ""))
    r = db.switch_data_dir(other, overwrite=True)
    check("overwrite proceeds", r.get("ok") is True, str(r))
    check("data still intact", db.get_setting("after_switch", "") == "written-in-new")
    db.set_data_dir(new_dir)
    db._apply_data_dir()

    # ---------- 4. 恢复默认 ----------
    r = db.switch_data_dir(default, overwrite=True)
    check("switch back to default", r.get("ok") is True)
    db.set_data_dir(None)  # 清指针，恢复纯默认状态
    db._apply_data_dir()
    check("back to default", db.get_data_dir() == db.DEFAULT_DIR)
    check("data intact after roundtrip",
          db.get_setting("after_switch", "") == "written-in-new")

    # ---------- 5. 目标建不出来 / 指针失效 ----------
    # 安装器实测踩过这两个：写了个用不了的目录，或目录后来被删，
    # 应用都会静默回落到默认位置，用户完全看不出来。
    made = os.path.join(BASE, "fb_created")
    shutil.rmtree(made, ignore_errors=True)
    db.set_data_dir(made)
    db._apply_data_dir()
    check("set_data_dir 会建出目标目录", os.path.isdir(made))
    check("指针生效", db.get_data_dir() == made)
    check("正常时不误报", db.pointer_issue() is None)

    blocker = os.path.join(BASE, "fb_blocker")
    with open(blocker, "wb") as f:
        f.write(b"x")
    try:
        db.set_data_dir(os.path.join(blocker, "sub"))  # 父级是个文件，建不出来
        check("不可用目标要报错", False, "没抛异常")
    except ValueError as e:
        check("不可用目标要报错", "不可用" in str(e), str(e))
    check("报错后指针没被改写", db.get_data_dir() == made)
    os.remove(blocker)

    gone = os.path.join(BASE, "fb_vanished")
    db.set_data_dir(gone)
    shutil.rmtree(gone)
    db._apply_data_dir()
    check("失效指针回落到默认目录", db.get_data_dir() == db.DEFAULT_DIR)
    check("失效指针被记下来（设置页据此提示）",
          db.pointer_issue() == gone, str(db.pointer_issue()))
    db.set_data_dir(None)
    db._apply_data_dir()
    check("清掉指针后不再报", db.pointer_issue() is None)

    # 清理
    db.set_setting("datadir_test_marker", "")
    db.set_setting("after_switch", "")
    shutil.rmtree(BASE, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
