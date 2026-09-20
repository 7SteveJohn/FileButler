"""数据库维护功能测试（沙盒数据目录，不碰真实库）：
备份 API / 未变更跳过 / VACUUM 压缩 / FTS 后台一致性重建。
全程保存并恢复 datadir 指针。"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db

PASS = 0
FAIL = 0
MARKER = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                      "FileButler", "datadir.txt")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    saved = None
    try:
        with open(MARKER, encoding="utf-8") as f:
            saved = f.read().strip()
    except OSError:
        pass

    tmp = tempfile.mkdtemp(prefix="fb_maint_")
    try:
        db.set_data_dir(tmp)
        db._apply_data_dir()
        db.close_all_conns()
        db.init_db()
        # file_index / watch_roots 表由 fileindex.SCHEMA_EXTRA 创建（db.SCHEMA 不含）
        from backend import fileindex as _fi
        c = db.get_conn()
        c.executescript(_fi.SCHEMA_EXTRA)
        c.commit()
        c.close()

        # 1. 官方 backup API：备份文件存在且是可用快照
        db.set_setting("marker", "v1")
        p1 = db.backup_db()
        check("backup created", bool(p1) and os.path.exists(p1))
        import sqlite3 as _s
        chk = _s.connect(p1)
        check("backup is snapshot", chk.execute(
            "SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "v1")
        chk.close()

        # 2. 未变更跳过
        time.sleep(0.05)  # 确保 mtime 推进
        p2 = db.backup_db(only_if_changed=True)
        check("unchanged backup skipped", p2 is None, str(p2))
        db.set_setting("marker", "v2")
        p3 = db.backup_db(only_if_changed=True)
        check("changed backup runs", bool(p3) and p3 != p1)
        chk = _s.connect(p3)
        check("changed backup has new data", chk.execute(
            "SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "v2")
        chk.close()

        # 3. VACUUM：制造 MB 级空洞 → 压缩 → 体积明显减小且数据完好
        #    （微缩库上 vacuum 的固定开销可能反而变大，空洞要足够大才可断言）
        conn = db.get_conn()
        pad = "x" * 200
        conn.executemany(
            "INSERT INTO file_index(root_id,path,name,ext,category,size,mtime) "
            "VALUES(?,?,?,?,?,?,?)",
            [(1, f"C:\\fb_maint_test\\{pad}\\f{i}.txt", f"f{i}.txt", "txt", "文档", 10, 1.0)
             for i in range(5000)])
        conn.commit()
        conn.execute("DELETE FROM file_index WHERE path LIKE ?",
                     ("C:\\fb_maint_test\\%",))
        conn.commit()
        conn.close()
        before = os.path.getsize(db.DB_PATH)
        r = db.vacuum()
        after = os.path.getsize(db.DB_PATH)
        check("vacuum ok", r.get("ok") is True, str(r))
        check("vacuum shrinks", after < before, f"{before} -> {after}")
        conn = db.get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM file_index").fetchone()["c"]
        conn.close()
        check("data intact after vacuum", n == 0, str(n))

        # 多线程形态回归位：应用的 vacuum_db 把 VACUUM 丢到后台新线程跑，而 UI /
        # watcher / 预热线程各持一条带 mmap 的长期缓存连接——只关自己那条没用，
        # SQLite 会因为别的连接还映射着文件而不截断。上面那条单线程 vacuum 抓不到
        # 这个（它当时就是绿的，而功能是坏的），所以这里刻意让主线程持有连接。
        conn = db.get_conn()
        conn.executemany(
            "INSERT INTO file_index(root_id,path,name,ext,category,size,mtime) "
            "VALUES(?,?,?,?,?,?,?)",
            [(1, f"C:\\fb_maint_mt\\{'z' * 200}\\f{i}.txt", f"f{i}.txt", "txt", "文档", 10, 1.0)
             for i in range(4000)])
        conn.commit()
        conn.execute("DELETE FROM file_index WHERE path LIKE ?", ("C:\\fb_maint_mt\\%",))
        conn.commit()
        before_mt = os.path.getsize(db.DB_PATH)
        box = {}
        th = threading.Thread(target=lambda: box.update(r=db.vacuum()))
        th.start()
        th.join()
        after_mt = os.path.getsize(db.DB_PATH)
        check("vacuum shrinks across threads", after_mt < before_mt,
              f"{before_mt} -> {after_mt} {box.get('r')}")
        check("其它连接 mmap 已恢复",
              conn.execute("PRAGMA mmap_size").fetchone()[0] == db.MMAP_SIZE)
        conn.close()

        # 4. FTS 后台一致性重建：人为制造漂移 → init_db 触发后台修复 → 轮询等齐
        conn = db.get_conn()
        for i in range(50):
            conn.execute("INSERT INTO file_index(root_id,path,name,ext,category,size,mtime) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (1, f"C:\\fb_fts_test\\x{i}.txt", f"x{i}.txt", "txt", "文档", 5, 1.0))
        conn.commit()
        conn.execute("DELETE FROM file_fts")  # 制造漂移
        conn.commit()
        conn.close()
        db.init_db()  # 应触发后台重建（不阻塞）
        ok = False
        for _ in range(60):  # 最多等 30s
            time.sleep(0.5)
            c = db.get_conn()
            try:
                f = c.execute("SELECT COUNT(*) c FROM file_fts").fetchone()["c"]
                n = c.execute("SELECT COUNT(*) c FROM file_index").fetchone()["c"]
            finally:
                c.close()
            if n and f == n:
                ok = True
                break
        check("fts background rebuild converges", ok, f"file_index={n} file_fts={f}")
    finally:
        # 无论成败都恢复真实数据目录指针
        db.close_all_conns()
        if saved:
            with open(MARKER, "w", encoding="utf-8") as fh:
                fh.write(saved)
        else:
            try:
                os.remove(MARKER)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        print("指针已恢复:", saved)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
