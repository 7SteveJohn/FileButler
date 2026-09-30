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


def _log(msg):
    """watcher 后台线程的故障记录。打包版 stdout 是 None，print 无人看见；
    落一份 watcher.log 到数据目录，「监控怎么不更新了」才有处可查。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        p = os.path.join(_db.get_data_dir(), "watcher.log")
        try:
            if os.path.getsize(p) > 262144:  # 超 256KB 重来，日志不该无限长
                os.remove(p)
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


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
        self._hook_lock = threading.Lock()
        self._hook_busy = {}    # 钩子名 -> 线程在飞标记（防 Ollama 卡死时线程堆积）

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
        # 在飞的钩子线程还在写库（自动 KB/图片描述），恢复备份/搬迁数据目录
        # 前必须等它们落地；给 8s 上限——Ollama 卡死时不能无限等
        deadline = time.time() + 8
        while time.time() < deadline:
            with self._hook_lock:
                if not any(self._hook_busy.values()):
                    break
            time.sleep(0.2)
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
            try:
                self._drain_queue()
                self._flush_stable()
                self._maybe_flush_hooks()
            except Exception as e:
                # worker 循环体兜底：任何意外异常都不允许让监控线程静默死亡
                # （死掉的表现是「新文件不再进索引」，用户无从发现）
                _log(f"watcher worker error: {e!r}")
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
            except Exception as e:
                # 单个事件失败不能断整个监控，但也不能无声：留痕便于发现索引漂移
                _log(f"event {kind} error {path!r}: {e!r}")

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
            except Exception as e:
                _log(f"index error {path!r}: {e!r}")

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
        # 防堆积：上一批还没跑完（如 Ollama 卡 600s）就不再起新线程，
        # 文件留在队列里等下一轮；先取批再起线程，起不成就不取
        if self._doc_queue and self.on_docs:
            docs = self._doc_queue[:50]
            if self._spawn_hook("docs", self.on_docs, docs):
                self._doc_queue = self._doc_queue[len(docs):]
        if self._img_queue and self.on_imgs:
            imgs = self._img_queue[:50]
            if self._spawn_hook("imgs", self.on_imgs, imgs):
                self._img_queue = self._img_queue[len(imgs):]

    def _spawn_hook(self, name, fn, items):
        """起一个钩子线程；同名钩子已在飞时返回 False（调用方保留队列）。"""
        with self._hook_lock:
            if self._hook_busy.get(name):
                return False
            self._hook_busy[name] = True

        def run():
            try:
                fn(items)
            except Exception as e:
                _log(f"hook {name} error: {e!r}")
            finally:
                with self._hook_lock:
                    self._hook_busy[name] = False

        threading.Thread(target=run, daemon=True,
                         name=f"fb-hook-{name}").start()
        return True


# 可自动进知识库的文档扩展名（与 knowledge.parsers.SUPPORTED 一致；此处复制避免 core→knowledge 依赖）
_DOC_EXTS = {
    "pdf", "docx", "pptx", "xlsx", "doc", "md", "markdown", "txt", "csv",
    "log", "ini", "cfg", "conf", "tsv", "json", "xml", "yaml", "yml", "toml",
    "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "scss", "java",
    "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "swift", "sh",
    "bat", "ps1", "sql", "vue",
}
