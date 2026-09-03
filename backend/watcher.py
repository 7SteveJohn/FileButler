"""实时文件监控：watchdog 事件 → 防抖队列 → 索引增量维护。

设计要点：
- 下载中的临时文件（.crdownload/.part/.tmp/~$xxx 等）忽略
- 文件需「稳定」（3 秒内无新事件且大小不变）才入库，避免抓到写一半的文件
- 单 worker 线程串行处理，不打爆磁盘
"""
import os
import queue
import threading
import time

from backend import db as _db


def _in_app_data_dir(path):
    """应用自己的数据目录下的事件不应广播给前端（db-shm/db-wal 等
    SQLite WAL 写入会被 watchdog 监听到，污染'最近动态'）。"""
    try:
        return os.path.normcase(path).startswith(
            os.path.normcase(_db.get_data_dir()))
    except Exception:
        return False


def _is_app_internal(path):
    """应用自身的临时/数据文件：
    - filebutler.db* （本应用 SQLite）
    - 任意 *.db-shm / *.db-wal / *.db-journal （其他 app 的 SQLite WAL 临时文件，watcher 误报）
    - thumbs.db / desktop.ini （Windows 缩略图缓存）
    - AppData 整个目录（应用自身数据目录）
    """
    base = os.path.basename(path).lower()
    if base.startswith('filebutler.db'):
        return True
    if base.endswith(('.db-shm', '.db-wal', '.db-journal')):
        return True
    if base in ('thumbs.db', 'desktop.ini'):
        return True
    if _in_app_data_dir(path):
        return True
    return False

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from backend import fileindex
from backend.core.scanner import is_temp_file


class _StableFile:
    """等待文件写入完成的条目。"""

    __slots__ = ("path", "size", "deadline")

    def __init__(self, path, settle_seconds=3.0):
        self.path = path
        self.size = _stat_size(path)
        self.deadline = time.time() + settle_seconds


def _stat_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


class _Handler(FileSystemEventHandler):
    def __init__(self, engine):
        self.engine = engine

    def on_created(self, event):
        if not event.is_directory:
            self.engine.touch(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.engine.touch(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            self.engine.remove_dir(event.src_path)
        else:
            self.engine.move(event.src_path, event.dest_path)

    def on_deleted(self, event):
        if event.is_directory:
            self.engine.remove_dir(event.src_path)
        else:
            self.engine.remove(event.src_path)


class WatchEngine:
    """监控引擎：观察所有启用根目录，防抖后增量更新 file_index。
    可选钩子：on_docs(新文档列表) / on_imgs(新图片列表) / on_removed(路径)，
    由上层装配自动知识库索引与图片语义描述。"""

    KB_FLUSH_SECONDS = 30.0
    KB_FLUSH_BATCH = 20

    def __init__(self, on_event=None, on_docs=None, on_imgs=None, on_removed=None,
                 on_organize=None):
        self.on_event = on_event        # 回调(kind, path, category)
        self.on_docs = on_docs          # 回调([path]) 文档/文本类新文件
        self.on_imgs = on_imgs          # 回调([path]) 新图片
        self.on_removed = on_removed    # 回调(path) 文件被删除
        self.on_organize = on_organize  # 回调(path, category) 自动整理钩子
        self.observer = None
        self._lock = threading.Lock()
        self._pending = {}              # path -> _StableFile
        self._queue = queue.Queue()     # 立即事件（删除/移动）
        self._doc_queue = []
        self._img_queue = []
        self._last_flush = time.time()
        self._stop = threading.Event()
        self._worker = None
        self._roots = []

    # ---------- 生命周期 ----------

    def start(self, roots=None):
        self.stop()
        self._roots = roots or fileindex.enabled_root_paths()
        self.observer = Observer(timeout=1)
        handler = _Handler(self)
        for r in self._roots:
            try:
                self.observer.schedule(handler, r, recursive=True)
            except OSError:
                continue
        self.observer.start()
        self._worker = threading.Thread(target=self._run, daemon=True, name="fb-watcher")
        self._worker.start()
        return len(self._roots)

    def stop(self):
        self._stop.set()
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=5)
            except Exception:
                pass
            self.observer = None
        if self._worker:
            self._worker.join(timeout=5)
            self._worker = None
        self._stop.clear()

    # ---------- 事件入口（watchdog 线程调用） ----------

    def touch(self, path):
        """文件出现/修改：进防抖池等待稳定。"""
        if is_temp_file(path) or _is_app_internal(path):
            return
        with self._lock:
            self._pending[path] = _StableFile(path)

    def remove(self, path):
        self._queue.put(("removed", path, None))

    def remove_dir(self, path):
        self._queue.put(("removed_dir", path, None))

    def move(self, src, dst):
        if not is_temp_file(dst) and not _is_app_internal(dst):
            with self._lock:
                self._pending[dst] = _StableFile(dst)
        # 跳过 app data 内的 src（不广播移除事件）
        if _is_app_internal(src):
            return
        self._queue.put(("removed", src, None))

    # ---------- 工作线程 ----------

    def _run(self):
        while not self._stop.is_set():
            self._drain_queue()
            self._flush_stable()
            self._maybe_flush_hooks()
            # 1s：文件稳定判定本身要 3s，无需 0.5s 高频轮询（降低空闲唤醒）
            time.sleep(1.0)

    def _drain_queue(self):
        while True:
            try:
                kind, path, _ = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                # 跳过应用自身的数据文件（SQLite WAL 等）
                if _is_app_internal(path):
                    continue
                if kind == "removed":
                    fileindex.remove_file(path)
                    if self.on_removed:
                        self.on_removed(path)
                elif kind == "removed_dir":
                    fileindex.remove_under(path)
                if self.on_event:
                    self.on_event(kind, path, None)
            except Exception:
                pass

    def _flush_stable(self):
        now = time.time()
        ready = []
        with self._lock:
            for path in list(self._pending):
                item = self._pending[path]
                if now < item.deadline:
                    continue
                # 大小仍在变化 → 重新等待
                cur = _stat_size(path)
                if cur is not None and cur != item.size:
                    item.size = cur
                    item.deadline = now + 3.0
                    continue
                del self._pending[path]
                if cur is not None:
                    ready.append(path)
        for path in ready:
            try:
                cat = fileindex.upsert_file(path)
                if cat:
                    ext = os.path.splitext(path)[1].lstrip(".").lower()
                    if ext in _DOC_EXTS:
                        self._doc_queue.append(path)
                    elif ext in fileindex.IMAGE_EXTS:
                        self._img_queue.append(path)
                    if self.on_event:
                        self.on_event("added", path, cat)
                    if self.on_organize:
                        self.on_organize(path, cat)
            except Exception:
                pass

    def _maybe_flush_hooks(self):
        """攒批触发自动知识库索引 / 图片描述（30 秒或满 20 个）。"""
        if not (self.on_docs or self.on_imgs):
            return
        due = (time.time() - self._last_flush >= self.KB_FLUSH_SECONDS
               or len(self._doc_queue) >= self.KB_FLUSH_BATCH
               or len(self._img_queue) >= self.KB_FLUSH_BATCH)
        if not due:
            return
        self._last_flush = time.time()
        docs, self._doc_queue = self._doc_queue[:50], self._doc_queue[50:]
        imgs, self._img_queue = self._img_queue[:50], self._img_queue[50:]
        if docs and self.on_docs:
            threading.Thread(target=lambda p=docs: _safe_call(self.on_docs, p),
                             daemon=True).start()
        if imgs and self.on_imgs:
            threading.Thread(target=lambda p=imgs: _safe_call(self.on_imgs, p),
                             daemon=True).start()


def _safe_call(fn, arg):
    try:
        fn(arg)
    except Exception:
        pass


# 可自动进知识库的文档扩展名（与 knowledge.parsers.SUPPORTED 一致；此处复制避免 core→knowledge 依赖）
_DOC_EXTS = {
    "pdf", "docx", "pptx", "xlsx", "doc", "md", "markdown", "txt", "csv",
    "log", "ini", "cfg", "conf", "tsv", "json", "xml", "yaml", "yml", "toml",
    "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "scss", "java",
    "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "swift", "sh",
    "bat", "ps1", "sql", "vue",
}
