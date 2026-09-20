"""SQLite 存储层：文件索引、知识库分块向量、操作日志、规则、设置。

向量检索用 numpy 暴力余弦（个人规模 <10 万分块为毫秒级），
不依赖 sqlite-vec 扩展，打包更可靠。

数据目录可迁移：默认 %APPDATA%/FileButler，其中 datadir.txt 指针文件
指向实际数据目录（数据库 + 缩略图缓存随迁）。
"""
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid

# numpy 惰性导入：仅向量检索/写入时加载（纯文件名搜索用户省 30-40MB 常驻内存）

DEFAULT_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FileButler")
MARKER_FILE = os.path.join(DEFAULT_DIR, "datadir.txt")

# 当前数据目录与数据库路径（模块级变量，切换时原位更新）
APP_DIR = DEFAULT_DIR
DB_PATH = os.path.join(APP_DIR, "filebutler.db")


# 指针指向但已不存在的目录（None = 正常）。get_data_dir 会回落默认位置，
# 但必须把这个异常留出来给设置页提示，否则用户只会看到"我明明选了 D 盘"。
_POINTER_ISSUE = None


def get_data_dir():
    """读取指针文件返回实际数据目录；无指针或指针失效则用默认目录。"""
    global _POINTER_ISSUE
    _POINTER_ISSUE = None
    try:
        with open(MARKER_FILE, "r", encoding="utf-8") as f:
            path = f.read().strip()
    except OSError:
        return DEFAULT_DIR
    if not path:
        return DEFAULT_DIR
    if os.path.isdir(path):
        return path
    _POINTER_ISSUE = path
    return DEFAULT_DIR


def pointer_issue():
    """指针指向、但当前已不存在的目录（None 表示指针正常）。"""
    return _POINTER_ISSUE


def set_data_dir(path):
    """写入指针文件（path=None 恢复默认）。

    先把目标目录建出来并试写一次：指针若指向一个不存在的目录，
    get_data_dir 会静默回落默认位置，用户根本不知道切换没生效。
    """
    os.makedirs(DEFAULT_DIR, exist_ok=True)
    if not path:
        if os.path.exists(MARKER_FILE):
            os.remove(MARKER_FILE)
        return
    abs_path = os.path.abspath(path)
    try:
        os.makedirs(abs_path, exist_ok=True)
        probe = os.path.join(abs_path, ".fb_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise ValueError(f"数据目录不可用：{abs_path}（{e}）") from e
    with open(MARKER_FILE, "w", encoding="utf-8") as f:
        f.write(abs_path)


def _apply_data_dir():
    global APP_DIR, DB_PATH
    APP_DIR = get_data_dir()
    DB_PATH = os.path.join(APP_DIR, "filebutler.db")


def find_data_dirs(drives=(), exclude=None, min_bytes=1 << 20):
    """有界地找一找别处是否还有装着数据的 FileButler 库。

    只用于「当前库是空的」这一异常场景（指针文件丢失 / 迁移半途失败），所以刻意
    不做递归全盘扫描：只看默认目录、各盘根本身、以及盘根的一级子目录里的
    filebutler.db——一次 readdir 加若干 stat，代价可控。
    返回按体积降序的前 5 个候选，调用方据此提示用户，切换与否仍由用户决定。
    """
    exc = os.path.normcase(os.path.abspath(exclude or DB_PATH))
    dirs = [DEFAULT_DIR]
    for d in drives:
        dirs.append(d)
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            dirs.append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    hits = {}
    for parent in dirs:
        p = os.path.join(parent, "filebutler.db")
        if os.path.normcase(os.path.abspath(p)) == exc:
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size < min_bytes:
            continue
        hits[os.path.normcase(p)] = {"dir": parent, "db_path": p, "size": size}
    return sorted(hits.values(), key=lambda x: -x["size"])[:5]


_apply_data_dir()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,          -- 扩展名（不含点，小写）
    category TEXT NOT NULL,
    sub TEXT,
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS kb_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    added_at REAL,
    last_index_at REAL,
    source TEXT DEFAULT 'manual'    -- manual 手动添加 / auto 跟随监控目录自动创建
);
CREATE TABLE IF NOT EXISTS kb_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER NOT NULL,
    path TEXT UNIQUE NOT NULL,
    mtime REAL,
    size INTEGER,
    hash TEXT,
    status TEXT DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    seq INTEGER,
    text TEXT,
    vec BLOB,
    FOREIGN KEY(file_id) REFERENCES kb_files(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    ts REAL,
    action TEXT,        -- move / rename / trash
    src TEXT,
    dst TEXT,           -- trash 操作时为目标文件夹
    undone INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS img_index (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    descr TEXT,          -- 视觉模型生成的中文描述
    ocr TEXT,            -- 视觉模型转录的图中文字（可搜索）
    vec BLOB
);
CREATE TABLE IF NOT EXISTS qa_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS qa_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT,
    content TEXT,
    sources TEXT,
    ts REAL
);
CREATE TABLE IF NOT EXISTS weekly_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT UNIQUE,
    created_at REAL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS organize_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    target_root TEXT,
    images_by_year INTEGER DEFAULT 1,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS favorites (
    path TEXT PRIMARY KEY,
    note TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS exclude_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    added_at REAL
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS file_tags (
    path TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (path, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_qam_session ON qa_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_ops_batch ON operations(batch_id);
CREATE INDEX IF NOT EXISTS idx_filetags_tag ON file_tags(tag_id);
"""

_conn_lock = threading.Lock()


# 读加速用的 mmap 窗口：实测 33MB 库上查询快 2.5-3.5 倍（11ms vs 28ms），
# 代价是「被连接映射着的库文件 SQLite 不会截断」——VACUUM 因此只重组页不缩文件。
# vacuum() 会临时撤掉所有活连接的映射再恢复，两边都不牺牲。
MMAP_SIZE = 268435456

_live_conns = set()        # 由 _connect() 登记的活连接（供 vacuum 撤映射用）
_live_lock = threading.Lock()


def _connect():
    os.makedirs(APP_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout 解决多线程写竞争（watcher 实时 upsert + rescan 后台 + 用户 query）
    # busy_timeout=5000ms：SQLite 写锁等 5s 才报 OperationalError
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL 模式下折中（FULL=安全，NORMAL=性能+安全平衡）
    # 读性能调优（速度）：mmap 让 FTS/大表查询免掉页拷贝（文件页由 OS 缓存共享，
    # 不额外占私有内存）；页缓存 4MB/连接——多线程各持连接，过大内存会翻倍
    conn.execute(f"PRAGMA mmap_size={MMAP_SIZE}")
    conn.execute("PRAGMA cache_size=-4000")
    with _live_lock:
        _live_conns.add(conn)
    return conn


def _suspend_mmap():
    """临时撤掉所有活连接的 mmap 映射，返回被撤过的连接（供 vacuum 后恢复）。

    vacuum 可能跑在后台新线程上，而 UI 线程 / watcher / 缓存预热线程各持一条
    长期缓存连接——只要有一条还映射着文件，SQLite 就不截断。
    别的线程在此期间照常查询，只是走普通的 read+copy。
    """
    suspended = []
    with _live_lock:
        for c in list(_live_conns):
            try:
                if c.execute("PRAGMA mmap_size").fetchone()[0]:
                    c.execute("PRAGMA mmap_size=0")
                    suspended.append(c)
            except Exception:
                _live_conns.discard(c)   # 已关闭的连接，顺手摘掉
    return suspended


def _resume_mmap(conns):
    with _live_lock:
        for c in conns:
            try:
                c.execute(f"PRAGMA mmap_size={MMAP_SIZE}")
            except Exception:
                _live_conns.discard(c)


# ---------- 线程本地连接复用 ----------
# 每次操作都新开连接会让页缓存每次冷启动（SQLite 官方论坛：新连接需重新从磁盘读数据），
# 且 watcher 高频事件下开关连接 + WAL pragma 的开销会累积成可观的 CPU 占用。
# 方案：每个线程缓存一个连接，get_conn() 返回代理，close() 归还线程槽而不是真关闭。
# 数据目录切换/恢复备份时 bump _conn_generation，旧连接在下次 get_conn 时被替换。

_conn_tls = threading.local()
_conn_generation = 0


class _ConnProxy:
    """sqlite3.Connection 代理：close() 归还线程槽，其余全部透传。"""

    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        # 归还：连接留在 _conn_tls 里复用；真正关闭由 _close_cached / 代际失效处理
        if getattr(_conn_tls, "proxy", None) is self:
            return
        try:
            self._conn.close()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


def _close_cached():
    """关闭当前线程缓存的连接（数据目录切换 / 退出时调用）。"""
    proxy = getattr(_conn_tls, "proxy", None)
    if proxy is not None:
        try:
            proxy._conn.close()
        except Exception:
            pass
        _conn_tls.proxy = None


def close_all_conns():
    """失效所有线程的缓存连接（只能在持有 _conn_lock 时调用，
    因为其他线程的缓存连接在下次 get_conn 时才会惰性替换）。"""
    global _conn_generation
    _conn_generation += 1


def get_conn():
    global _conn_generation
    proxy = getattr(_conn_tls, "proxy", None)
    if proxy is not None and getattr(_conn_tls, "gen", -1) == _conn_generation:
        return proxy
    _close_cached()
    conn = _connect()
    proxy = _ConnProxy(conn)
    _conn_tls.proxy = proxy
    _conn_tls.gen = _conn_generation
    return proxy


_UNSAFE_DIR_PARTS = {"windows", "program files", "program files (x86)", "programdata"}


def _validate_data_dir(path):
    """迁移目标目录安全校验：拒绝空路径 / 相对路径 / .. / 盘根 / 系统目录。
    返回 (规范化绝对路径, None) 或 (None, 错误信息)。"""
    raw = (path or "").strip()
    if not raw:
        return None, "目录不能为空"
    if ".." in raw.replace("/", "\\").split("\\"):
        return None, "目录路径不能包含 .."
    p = os.path.abspath(os.path.normpath(raw))
    drive, tail = os.path.splitdrive(p)
    parts = [x for x in tail.replace("/", "\\").split("\\") if x]
    if not parts:
        return None, "不能使用盘根（如 D:\\）作为数据目录"
    if parts[0].lower() in _UNSAFE_DIR_PARTS:
        return None, "不能使用系统目录作为数据目录"
    return p, None


def switch_data_dir(new_dir, overwrite=False, mode="copy", on_status=None):
    """安全搬迁数据目录：checkpoint WAL → 复制 → 完整性校验 → 更新指针。
    mode=copy：旧目录数据保留作为天然备份（默认）。
    mode=move：把旧目录记入 pending_delete_dir，下次启动后台删除——
    本进程还握着旧库的连接句柄，当场删必然失败；且删除前新库必须已经
    真正在用（重启后），否则误删唯一副本。需调用方先停掉 watcher 等写入源。"""
    new_dir, err = _validate_data_dir(new_dir)
    if err:
        return {"ok": False, "error": err}
    if mode not in ("copy", "move"):
        return {"ok": False, "error": "无效的搬迁方式"}
    if os.path.normcase(new_dir) == os.path.normcase(APP_DIR):
        return {"ok": False, "error": "新目录与当前目录相同"}
    if os.path.normcase(APP_DIR).startswith(os.path.normcase(new_dir + os.sep)) or \
       os.path.normcase(new_dir).startswith(os.path.normcase(APP_DIR + os.sep)):
        return {"ok": False, "error": "新旧目录不能互相嵌套"}

    def say(msg):
        if on_status:
            on_status(msg)

    say("准备目标目录")
    try:
        os.makedirs(new_dir, exist_ok=True)
        probe = os.path.join(new_dir, ".fb_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        return {"ok": False, "error": f"目标目录不可写：{e}"}

    dst_db = os.path.join(new_dir, "filebutler.db")
    if os.path.exists(dst_db) and not overwrite:
        return {"ok": False,
                "error": "目标目录已存在 filebutler.db（如确定覆盖请重试并选择覆盖）"}

    old_dir = APP_DIR  # 指针切换前记住旧目录
    with _conn_lock:
        say("合并未落盘数据")
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()

        say("复制数据库")
        try:
            shutil.copy2(DB_PATH, dst_db)
        except OSError as e:
            return {"ok": False, "error": f"复制数据库失败：{e}"}

        say("校验数据完整性")
        check = sqlite3.connect(dst_db)
        try:
            row = check.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                os.remove(dst_db)
                return {"ok": False, "error": f"校验失败：{row[0] if row else '未知错误'}，已回滚"}
        finally:
            check.close()

        say("复制缩略图缓存")
        old_thumbs = os.path.join(APP_DIR, "thumbs")
        if os.path.isdir(old_thumbs):
            try:
                shutil.copytree(old_thumbs, os.path.join(new_dir, "thumbs"),
                                dirs_exist_ok=True)
            except OSError:
                pass  # 缩略图仅是缓存，失败可忽略（会自动重新生成）

        say("切换指针")
        set_data_dir(new_dir)
        _apply_data_dir()
        close_all_conns()  # 各线程缓存的连接指向旧库文件，必须失效
        vec_index.invalidate()
        img_vec_index.invalidate()
        if mode == "move":
            set_setting("pending_delete_dir", old_dir)

    say("完成")
    return {"ok": True, "new_dir": new_dir, "mode": mode}


def consume_pending_delete_dir():
    """取出并清除待删除的旧数据目录（迁移模式遗留）。
    校验：目录存在、不是当前数据目录，才允许删。"""
    old = get_setting("pending_delete_dir")
    if not old:
        return None
    set_setting("pending_delete_dir", "")
    old = old.strip()
    if not old or not os.path.isdir(old):
        return None
    if os.path.normcase(os.path.abspath(old)) == os.path.normcase(APP_DIR):
        return None  # 指针实际又指回去了，绝不能删
    return old


def remove_data_dir_tree(old_dir):
    """删除迁移后的旧数据目录（仅接受 consume_pending_delete_dir 放行的路径）。
    盘根/系统目录绝不删；默认位置保留外壳与 datadir.txt（指针永远住默认目录，
    删了它 get_data_dir 就找不到自定义目录了）。"""
    old_dir = os.path.abspath(os.path.normpath((old_dir or "").strip()))
    if not os.path.isdir(old_dir):
        return
    drive, tail = os.path.splitdrive(old_dir)
    parts = [x for x in tail.replace("/", "\\").split("\\") if x]
    if not parts or parts[0].lower() in _UNSAFE_DIR_PARTS:
        return  # 盘根或系统目录：拒绝删除
    is_default = os.path.normcase(old_dir) == os.path.normcase(DEFAULT_DIR)
    if is_default:
        for name in os.listdir(old_dir):
            if name == "datadir.txt":
                continue
            p = os.path.join(old_dir, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except OSError:
                pass
    else:
        shutil.rmtree(old_dir, ignore_errors=True)


def init_db():
    with _conn_lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            # 旧库迁移：kb_folders 补 source 列，img_index 补 ocr 列
            cols = {r[1] for r in conn.execute("PRAGMA table_info(kb_folders)")}
            if "source" not in cols:
                conn.execute("ALTER TABLE kb_folders ADD COLUMN source TEXT DEFAULT 'manual'")
            icols = {r[1] for r in conn.execute("PRAGMA table_info(img_index)")}
            if "ocr" not in icols:
                conn.execute("ALTER TABLE img_index ADD COLUMN ocr TEXT")
            conn.commit()
            _ensure_fts(conn)
            conn.commit()
        finally:
            conn.close()


def ensure_fts():
    """file_index 表就绪后补建 FTS（首次全新安装时 init_db 跑在
    file_index 建表之前，触发器引用的表不存在会整体跳过——
    由 fileindex.ensure_default_roots 建好表后回调这里）。"""
    conn = _connect()
    try:
        _ensure_fts(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_fts(conn):
    """FTS5 全文索引（trigram，支持中文子串），独立表 + 触发器与 file_index 自动同步。

    注意：早期版本用外部内容表（content='file_index'）+ 'rebuild' 命令，在
    SQLite 3.50 + trigram + WAL 组合下有损坏风险（rebuild 后触发器任何写操作
    即报 database disk image is malformed）。这里统一迁移为独立表：
    - 触发器用显式 DELETE（不依赖 content 表同步）
    - 一致性重建逐行 INSERT（不用 'rebuild' 特殊命令）
    """
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='file_fts'").fetchone()
        if row and "content=" in (row["sql"] or ""):
            # 旧式外部内容表 → 迁移为独立表
            conn.execute("DROP TABLE IF EXISTS file_fts")
            conn.execute("DROP TRIGGER IF EXISTS fi_ai")
            conn.execute("DROP TRIGGER IF EXISTS fi_ad")
            conn.execute("DROP TRIGGER IF EXISTS fi_au")
        conn.executescript("""
CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(
    name, path, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS fi_ai AFTER INSERT ON file_index BEGIN
    INSERT INTO file_fts(rowid, name, path) VALUES (new.id, new.name, new.path);
END;
CREATE TRIGGER IF NOT EXISTS fi_ad AFTER DELETE ON file_index BEGIN
    DELETE FROM file_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS fi_au AFTER UPDATE ON file_index BEGIN
    DELETE FROM file_fts WHERE rowid = old.id;
    INSERT INTO file_fts(rowid, name, path) VALUES (new.id, new.name, new.path);
END;
""")
        # 一致性检查：数量不一致（进程被杀导致触发器中断等）时后台重建。
        # 83 万行 COUNT 对比本身也要数百 ms～秒级，同步做会拖慢窗口创建——
        # 连同重建一起移到后台线程。期间搜索可能命中部分旧数据（可接受）。
        threading.Thread(target=_check_fts_consistency, daemon=True,
                         name="fb-fts-check").start()
    except Exception as e:
        print("FTS5 init skipped:", e)  # FTS 不可用时搜索自动退回 LIKE


def _check_fts_consistency():
    try:
        conn = _connect()
        try:
            n = conn.execute("SELECT COUNT(*) c FROM file_index").fetchone()["c"]
            f = conn.execute("SELECT COUNT(*) c FROM file_fts").fetchone()["c"]
        finally:
            conn.close()
        if n != f:
            _fts_rebuild_async()
    except Exception as e:
        print("FTS consistency check skipped:", e)


_fts_rebuild_lock = threading.Lock()
_fts_rebuilding = False


def _fts_rebuild_async():
    """后台一致性重建（单实例防重入）。"""
    global _fts_rebuilding
    if _fts_rebuilding or not _fts_rebuild_lock.acquire(blocking=False):
        return

    def _run():
        global _fts_rebuilding
        _fts_rebuilding = True
        try:
            conn = _connect()
            try:
                conn.execute("BEGIN IMMEDIATE")  # 单事务：期间触发器写入排队等待
                conn.execute("DELETE FROM file_fts")
                conn.executemany(
                    "INSERT INTO file_fts(rowid, name, path) VALUES(?,?,?)",
                    conn.execute("SELECT id, name, path FROM file_index"))
                conn.commit()
                print("[fts] background rebuild done")
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print("[fts] background rebuild failed:", e)
            finally:
                conn.close()
        finally:
            _fts_rebuilding = False
            _fts_rebuild_lock.release()

    threading.Thread(target=_run, daemon=True, name="fb-fts-rebuild").start()


# ---------- 设置 ----------

def get_setting(key, default=None):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- 规则 ----------

def get_rules():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM rules WHERE enabled=1 ORDER BY id")]
    finally:
        conn.close()


def add_rule(pattern, category, sub=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rules(pattern,category,sub) VALUES(?,?,?)",
            (pattern.lower().lstrip("."), category, sub),
        )
        conn.commit()
    finally:
        conn.close()


def delete_rule(rule_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- 操作日志（整理/撤销） ----------

def new_batch_id():
    return uuid.uuid4().hex[:12]


def log_operation(batch_id, action, src, dst):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO operations(batch_id,ts,action,src,dst) VALUES(?,?,?,?,?)",
            (batch_id, time.time(), action, src, dst),
        )
        conn.commit()
    finally:
        conn.close()


def list_operations(batch_id=None, limit=500):
    conn = get_conn()
    try:
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM operations WHERE batch_id=? AND undone=0 ORDER BY id DESC",
                (batch_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_batches(limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT batch_id, MIN(ts) AS ts, COUNT(*) AS n, "
            "SUM(undone) AS undone_n FROM operations "
            "GROUP BY batch_id ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_undone(batch_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE operations SET undone=1 WHERE batch_id=?", (batch_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- 向量检索 ----------

class VectorIndex:
    """内存缓存全部向量，余弦相似度检索。写入后调用 invalidate()。
    select_sql 需返回 (id, vec) 两列。"""

    def __init__(self, select_sql):
        self._select_sql = select_sql
        self._ids = None
        self._matrix = None
        self._lock = threading.Lock()

    def invalidate(self):
        with self._lock:
            self._ids = None
            self._matrix = None

    def _ensure_loaded(self):
        with self._lock:
            if self._ids is not None:
                return
            import numpy as np
            conn = _connect()
            try:
                rows = conn.execute(self._select_sql).fetchall()
            finally:
                conn.close()
            if not rows:
                self._ids = np.array([], dtype=np.int64)
                self._matrix = np.zeros((0, 1), dtype=np.float16)
                return
            ids = np.array([r["id"] for r in rows], dtype=np.int64)
            vecs = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._ids = ids
            # float16 存储：向量矩阵内存减半（余弦排序精度损失可忽略）
            self._matrix = (vecs / norms).astype(np.float16)

    def search(self, query_vec, k=8):
        """返回 [(id, score)]，按相似度降序。分块 float16 点积：内存恒定。"""
        import numpy as np
        self._ensure_loaded()
        with self._lock:
            ids = self._ids
            matrix = self._matrix
            if ids is None or matrix is None or len(ids) == 0:
                return []
            q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
            n = np.linalg.norm(q)
            if n == 0:
                return []
            q = (q / n).astype(np.float16)
            k = min(k, len(ids))
            best = []
            step = 8192
            for start in range(0, len(ids), step):
                scores = matrix[start:start + step] @ q
                m = len(scores)
                top = min(k, m)
                idx = np.argpartition(-scores, top - 1)[:top]
                best += [(start + int(i), float(scores[i])) for i in idx]
            best.sort(key=lambda x: -x[1])
            return [(int(ids[g]), s) for g, s in best[:k]]


vec_index = VectorIndex("SELECT id, vec FROM chunks WHERE vec IS NOT NULL")
img_vec_index = VectorIndex("SELECT rowid AS id, vec FROM img_index WHERE vec IS NOT NULL")


def img_rows_by_paths(paths):
    conn = get_conn()
    try:
        marks = ",".join("?" * len(paths))
        rows = conn.execute(
            f"SELECT path, mtime, descr FROM img_index WHERE path IN ({marks})",
            list(paths)).fetchall()
        return {r["path"]: dict(r) for r in rows}
    finally:
        conn.close()


def img_rows_by_rowids(rowids):
    """向量检索结果（rowid）→ {rowid: {path, descr, ocr}}。"""
    conn = get_conn()
    try:
        marks = ",".join("?" * len(rowids))
        rows = conn.execute(
            f"SELECT rowid, path, descr, COALESCE(ocr,'') AS ocr FROM img_index "
            f"WHERE rowid IN ({marks})", list(rowids)).fetchall()
        return {r["rowid"]: {"path": r["path"], "descr": r["descr"], "ocr": r["ocr"]}
                for r in rows}
    finally:
        conn.close()


def img_keyword_search(query, k=4):
    """降级模式：图片描述/OCR 文字的关键词匹配。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT path, descr, COALESCE(ocr,'') AS ocr FROM img_index "
            "WHERE descr LIKE ? OR ocr LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", k)).fetchall()
        return [{"path": r["path"], "descr": r["descr"], "ocr": r["ocr"]} for r in rows]
    finally:
        conn.close()


def upsert_img(path, mtime, size, descr, vec, ocr=""):
    import numpy as _np
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO img_index(path,mtime,size,descr,ocr,vec) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
            "descr=excluded.descr, ocr=excluded.ocr, vec=excluded.vec",
            (path, mtime, size, descr, ocr,
             _np.asarray(vec, dtype=_np.float32).tobytes() if vec is not None else None))
        conn.commit()
    finally:
        conn.close()


# ---------- 数据库备份 ----------

BACKUP_KEEP = 5
# 备份总占用上限：份数之外再套一层体积预算，避免大库把 5 份变成 5GB
BACKUP_MAX_TOTAL = 2 * 1024 ** 3


def backup_dir():
    return os.path.join(get_data_dir(), "backups")


def backup_db(only_if_changed=False):
    """在线备份到 backups/（官方 sqlite3 backup API：活库/WAL 下安全，
    见 docs.python.org sqlite3.Connection.backup），保留最近 BACKUP_KEEP 份
    且总占用不超过 BACKUP_MAX_TOTAL（至少留最新一份）。
    only_if_changed=True 且库自上次备份后无写入（主文件+WAL 的 size/mtime
    均未变）时跳过，返回 None——避免退出时每天无谓复制大库。
    变更标记存在备份目录的独立文件里（存库内会因写入标记本身改变库而失效）。"""
    import re as _re
    import time as _time
    if only_if_changed:
        sig_file = _backup_sig_file()
        try:
            with open(sig_file, encoding="utf-8") as f:
                last = f.read().strip()
        except OSError:
            last = ""
        if last and last == _db_change_sig():
            return None
    bdir = backup_dir()
    os.makedirs(bdir, exist_ok=True)
    stamp = _time.strftime("%Y%m%d-%H%M%S")
    n = 0
    while True:
        suffix = f"-{n}" if n else ""
        dst = os.path.join(bdir, f"filebutler-{stamp}{suffix}.db")
        if not os.path.exists(dst):
            break
        n += 1
    # 先写临时文件再改名：备份可能在退出/关闭时被中断，半截文件
    # 不能顶着正式名字混进备份列表
    tmp = dst + ".tmp"
    src = _connect()
    target = sqlite3.connect(tmp)
    try:
        src.backup(target)  # 在线备份：含 WAL 未 checkpoint 的内容，页级一致快照
    except Exception:
        target.close()
        src.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        try:
            target.close()
        except Exception:
            pass
        try:
            src.close()
        except Exception:
            pass
    os.replace(tmp, dst)
    try:
        with open(_backup_sig_file(), "w", encoding="utf-8") as f:
            f.write(_db_change_sig())
    except OSError:
        pass
    # 轮转：既限份数也限总体积（见 rotate_backups）
    rotate_backups(bdir)
    return dst


def rotate_backups(bdir=None):
    """按「份数 + 总体积」双预算轮转备份，返回保留下来的文件名列表。

    只按份数保留的话，磁盘占用会随库大小线性膨胀——838MB 的库配 BACKUP_KEEP=5
    就是静默的 4.2GB（本机实测就是这么堆出来的）。所以再套一层体积预算；
    但无论如何都至少留下最新一份，否则等于把唯一的备份也删了。
    """
    import re as _re
    bdir = bdir or backup_dir()
    if not os.path.isdir(bdir):
        return []
    backups = sorted(f for f in os.listdir(bdir)
                     if _re.fullmatch(r"filebutler-\d{8}-\d{6}(-\d+)?\.db", f))
    kept, kept_bytes = [], 0
    for i, name in enumerate(reversed(backups)):      # 升序列表，倒着走 = 新→旧
        path = os.path.join(bdir, name)
        try:
            sz = os.path.getsize(path)
        except OSError:
            continue
        if i and (i >= BACKUP_KEEP or kept_bytes + sz > BACKUP_MAX_TOTAL):
            try:
                os.remove(path)
            except OSError:
                kept.append(name)     # 删不掉就当留着，别让计数骗人
        else:
            kept.append(name)
            kept_bytes += sz
    return kept


def _backup_sig_file():
    return os.path.join(backup_dir(), "last_backup.sig")


def _db_change_sig():
    """库是否被写过的粗粒度信号：主文件与 WAL 文件的 (size, mtime) 拼接。"""
    parts = []
    for suffix in ("", "-wal"):
        p = DB_PATH + suffix
        try:
            st = os.stat(p)
            parts.append(f"{st.st_size}:{int(st.st_mtime)}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def vacuum(on_done=None):
    """压缩数据库（VACUUM + WAL checkpoint 截断）。
    官方文档：VACUUM 需要约 2 倍库大小的空闲磁盘（整库拷到临时文件再回写）；
    WAL 模式下变更先写 WAL，checkpoint 后主文件才真正缩小。
    on_done(result_dict) 在完成/失败时回调。返回 {"ok", "before", "after", "error"}。"""
    import shutil as _shutil

    def _size():
        try:
            return os.path.getsize(DB_PATH)
        except OSError:
            return 0

    before = _size()
    try:
        free = _shutil.disk_usage(os.path.dirname(DB_PATH) or ".").free
        if free < before * 2:
            return {"ok": False, "before": before, "after": before,
                    "error": f"磁盘空闲空间不足（VACUUM 需约 2 倍库大小："
                             f"{before * 2 // 1048576} MB）"}
        # SQLite 不截断「仍被某个连接内存映射着」的库文件。_connect() 开 256MB mmap 换
        # 2.5-3.5 倍读加速，而连接是按线程长期缓存的（UI / watcher / 预热线程各一条），
        # vacuum_db 又跑在后台新线程上——只关自己那条没用。所以先撤掉所有活连接的映射，
        # VACUUM 完再恢复。
        suspended = _suspend_mmap()
        conn = _connect()
        try:
            conn.execute("PRAGMA mmap_size=0")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
            with _live_lock:
                _live_conns.discard(conn)
            _resume_mmap(suspended)
        # 结构已重写，作废"未变更跳过"标记，让下次退出备份必定执行
        try:
            os.remove(_backup_sig_file())
        except OSError:
            pass
        after = _size()
        result = {"ok": True, "before": before, "after": after}
        if after >= before:
            result["note"] = ("页已重组但文件没缩小：多半是监控/扫描线程还持有映射连接，"
                              "等它们下次取连接后再压缩一次")
    except Exception as e:
        result = {"ok": False, "before": before, "after": _size(), "error": str(e)}
    if on_done:
        try:
            on_done(result)
        except Exception:
            pass
    return result


def db_file_sizes():
    """主库与 WAL 文件当前大小（字节）。"""
    out = {}
    for key, suffix in (("db", ""), ("wal", "-wal")):
        try:
            out[key] = os.path.getsize(DB_PATH + suffix)
        except OSError:
            out[key] = 0
    return out


def list_backups():
    bdir = backup_dir()
    if not os.path.isdir(bdir):
        return []
    # 清理被中断的备份残留（写一半的 .tmp；留 1 小时缓冲防误删正在写的）
    import time as _tn
    for f in os.listdir(bdir):
        if f.endswith(".db.tmp"):
            p = os.path.join(bdir, f)
            try:
                if _tn.time() - os.path.getmtime(p) > 3600:
                    os.remove(p)
            except OSError:
                pass
    import time as _t
    out = []
    for f in sorted(os.listdir(bdir), reverse=True):
        if f.startswith("filebutler-") and f.endswith(".db"):
            p = os.path.join(bdir, f)
            try:
                out.append({"file": f, "path": p,
                            "size": os.path.getsize(p),
                            "mtime": os.path.getmtime(p)})
            except OSError:
                pass
    return out


def restore_backup(backup_path):
    """用备份覆盖当前库（需重启生效）。当前库先另存一份以防万一。"""
    if not os.path.isfile(backup_path):
        return {"ok": False, "error": "备份文件不存在"}
    with _conn_lock:
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
        import time as _t
        safety = DB_PATH + ".pre-restore"
        shutil.copy2(DB_PATH, safety)
        shutil.copy2(backup_path, DB_PATH)
        for suffix in ("-wal", "-shm"):
            for p in (DB_PATH + suffix, safety + suffix):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        vec_index.invalidate()
        img_vec_index.invalidate()
        close_all_conns()  # 覆盖了库文件，缓存连接必须失效（配合 need_restart）
    return {"ok": True, "need_restart": True, "safety_copy": safety}


def remove_img(path):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM img_index WHERE path=?", (path,))
        conn.commit()
    finally:
        conn.close()


def img_index_stats():
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) c FROM img_index").fetchone()["c"]
    finally:
        conn.close()


def search_chunks_by_ids(ids):
    conn = get_conn()
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT c.id, c.seq, c.text, f.path AS file_path "
            f"FROM chunks c JOIN kb_files f ON f.id=c.file_id "
            f"WHERE c.id IN ({marks})", list(ids)).fetchall()
        return {r["id"]: dict(r) for r in rows}
    finally:
        conn.close()


def keyword_search(query, k=8):
    """降级检索：Ollama 不可用时用 LIKE 全文匹配。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.seq, c.text, f.path AS file_path FROM chunks c "
            "JOIN kb_files f ON f.id=c.file_id WHERE c.text LIKE ? LIMIT ?",
            (f"%{query}%", k)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stats():
    conn = get_conn()
    try:
        n_files = conn.execute("SELECT COUNT(*) c FROM kb_files").fetchone()["c"]
        n_chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        n_folders = conn.execute("SELECT COUNT(*) c FROM kb_folders").fetchone()["c"]
        n_ops = conn.execute("SELECT COUNT(*) c FROM operations").fetchone()["c"]
        return {"files": n_files, "chunks": n_chunks, "folders": n_folders, "operations": n_ops,
                "db_path": DB_PATH}
    finally:
        conn.close()

def file_in_any_kb_folder(path):
    """判断文件是否在某个 KB 文件夹（手动或 auto）内。用于自动 KB 钩子过滤。"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT path FROM kb_folders").fetchall()
    finally:
        conn.close()
    norm = os.path.normcase(os.path.normpath(path))
    for r in rows:
        kb = os.path.normcase(os.path.normpath(r["path"]))
        if norm == kb or norm.startswith(kb.rstrip("/\\") + os.sep):
            return True
    return False
