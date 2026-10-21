"""崩溃日志测试：文件落盘、主线程与后台线程未捕获异常都能留下堆栈。"""
import logging
import os
import sys
import tempfile
import threading
import time

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_applog_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import applog, db          # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def read(path):
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    db.init_db()
    path = applog.setup("9.9.9")

    check("log file created", os.path.isfile(path), path)
    check("log under data dir", os.path.normcase(path).startswith(
        os.path.normcase(os.path.join(db.get_data_dir(), "logs"))), path)
    check("startup line written", "FileButler 9.9.9 启动" in read(path))

    # 重复 setup 不重复挂 handler
    n_handlers = len(logging.getLogger().handlers)
    path2 = applog.setup("9.9.9")
    check("setup idempotent", path2 == path and len(logging.getLogger().handlers) == n_handlers)

    # 主线程未捕获异常
    try:
        1 / 0
    except ZeroDivisionError:
        sys.excepthook(*sys.exc_info())
    text = read(path)
    check("main thread traceback logged",
          "未捕获异常" in text and "ZeroDivisionError" in text, text[-200:])

    # 后台线程未捕获异常
    def boom():
        raise ValueError("thread-boom")

    t = threading.Thread(target=boom, name="fb-test-boom")
    t.start()
    t.join()
    time.sleep(0.2)
    text = read(path)
    check("thread traceback logged",
          "fb-test-boom" in text and "thread-boom" in text, text[-200:])

    # 普通日志按级别落盘
    applog.get("demo").warning("hello-log")
    check("normal log written", "hello-log" in read(path))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
