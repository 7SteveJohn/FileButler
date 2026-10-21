"""应用日志：未捕获异常与关键事件落到数据目录的 logs/app.log。

windowed 打包后 sys.stderr 是 None，任何 print/traceback 都会直接丢掉，
程序闪退只能翻 Windows 事件查看器（1.3.0 的 MSVCP140 冲突就是这么查出来的）。
这里用标准库 logging 落盘（2 MB × 3 份轮转），并接管
sys.excepthook / threading.excepthook —— 后台线程崩掉不再静默。
"""
import logging
import logging.handlers
import os
import sys
import tempfile
import threading

_NAME = "filebutler"
_ready = False


def log_dir():
    from backend import db
    return os.path.join(db.get_data_dir(), "logs")


def get(name=None):
    """取本应用的 logger（logging.getLogger(NAME) 的语义）。"""
    return logging.getLogger(_NAME + ("." + name if name else ""))


def setup(version=""):
    """初始化文件日志，返回日志文件路径。重复调用只生效一次。

    数据目录不可写时（只读盘/盘被拔掉）退到系统临时目录——日志不值得
    让整个程序起不来，但也不应该静默丢失。
    """
    global _ready
    if _ready:
        return _path[0]

    path = os.path.join(log_dir(), "app.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        path = os.path.join(tempfile.gettempdir(), "FileButler-app.log")

    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _path[0] = path
    _ready = True

    root.info("FileButler %s 启动 | python %s | data_dir %s",
              version or "?", sys.version.split()[0], log_dir())
    return path


_path = [""]


def _excepthook(exc_type, exc, tb):
    """主线程未捕获异常（含 PyInstaller 下的启动期崩溃）。"""
    get("crash").critical("未捕获异常", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args):
    """后台线程未捕获异常：线程会死，但至少留一条记录。"""
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    get("crash").critical("线程 %s 未捕获异常", name,
                          exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
