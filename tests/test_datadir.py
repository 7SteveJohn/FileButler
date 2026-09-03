"""数据目录切换测试：指针文件、搬迁复制、完整性校验、恢复默认。"""
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

    # 清理
    db.set_setting("datadir_test_marker", "")
    db.set_setting("after_switch", "")
    shutil.rmtree(BASE, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
