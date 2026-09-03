"""文件总索引：全盘编目（只读，绝不移动文件）+ 文件名搜索。

watch_roots 表管理监控根目录（默认系统用户目录，可增删），
file_index 表存每个文件的元数据，由 full_scan / watcher 增量维护。
"""
import ctypes
import os
import time
from ctypes import wintypes

from backend import db
from backend.core import rules as rules_mod
from backend.core import scanner
from backend.core.scanner import is_temp_file

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS watch_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    label TEXT,
    enabled INTEGER DEFAULT 1,
    added_at REAL
);
CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id INTEGER,
    path TEXT UNIQUE NOT NULL,
    name TEXT,
    ext TEXT,
    category TEXT,
    size INTEGER,
    mtime REAL
);
CREATE INDEX IF NOT EXISTS idx_fi_root ON file_index(root_id);
CREATE INDEX IF NOT EXISTS idx_fi_category ON file_index(category);
"""

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tiff", "ico", "svg"}


# ---------- 系统用户目录（兼容 OneDrive 重定向） ----------

_KNOWN = {
    "Desktop": "{754AC886-DF64-4CBA-86B5-F7FBF4FBCEF5}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "Music": "{4BD8D571-6D19-48D3-BE97-4222200AFB54}",
}


def _known_folder(guid_str):
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    import uuid as _uuid
    g = GUID()
    u = _uuid.UUID(guid_str)
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i, b in enumerate(u.bytes[8:16]):
        g.Data4[i] = b
    ptr = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(ptr)) != 0:
        return None
    try:
        return ptr.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ptr)


def default_roots():
    """系统用户目录（存在且可访问的）。"""
    roots = []
    for label, guid in _KNOWN.items():
        p = _known_folder(guid)
        if p and os.path.isdir(p):
            roots.append((p, label))
    if not roots:  # 兜底
        home = os.path.expanduser("~")
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
            p = os.path.join(home, name)
            if os.path.isdir(p):
                roots.append((p, name))
    return roots


# ---------- 监控根目录管理 ----------

def enumerate_drives():
    """枚举所有本地固定盘（C:\\ D:\\ ...），跳过 CD-ROM / 网络盘 / U 盘（DRIVE_FIXED=3）。
    用户首次启动后开箱即扫描全盘，不需要手动加监控根。"""
    import ctypes
    import string
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                letter = string.ascii_uppercase[i]
                root = f"{letter}:\\"
                try:
                    dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
                except Exception:
                    continue
                if dtype == 3 and os.path.isdir(root):
                    drives.append(root)
    except Exception:
        pass
    return drives


def ensure_default_roots():
    """首次运行写入默认用户目录；幂等补全所有本地盘根（开箱即用）。"""
    conn = db.get_conn()
    try:
        conn.executescript(SCHEMA_EXTRA)
        n = conn.execute("SELECT COUNT(*) c FROM watch_roots").fetchone()["c"]
        now = time.time()
        added = False
        if n == 0:
            # 首次：加用户目录（Desktop/Documents/...）
            for p, label in default_roots():
                conn.execute("INSERT OR IGNORE INTO watch_roots(path,label,added_at) VALUES(?,?,?)",
                             (p, label, now))
            added = True
        # 幂等：补全所有本地盘根（C:\ D:\ ...），用户已加的子目录不会被覆盖
        # label 标"自动"让用户在设置里能识别/删除
        for drive in enumerate_drives():
            if conn.execute("SELECT 1 FROM watch_roots WHERE path=?",
                            (drive,)).fetchone():
                continue
            conn.execute("INSERT INTO watch_roots(path,label,added_at) VALUES(?,?,?)",
                         (drive, f"{drive[:-1]}（自动）", now))
            added = True
        conn.commit()
        return added
    finally:
        conn.close()


def list_roots(only_enabled=True):
    conn = db.get_conn()
    try:
        sql = "SELECT * FROM watch_roots"
        if only_enabled:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql + " ORDER BY id").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["n_files"] = conn.execute(
                "SELECT COUNT(*) c FROM file_index WHERE root_id=?", (r["id"],)).fetchone()["c"]
            result.append(d)
        return result
    finally:
        conn.close()


def add_root(path):
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return {"error": "不是有效目录"}
    conn = db.get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO watch_roots(path,label,added_at) VALUES(?,?,?)",
                     (path, os.path.basename(path) or path, time.time()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def remove_root(root_id, delete_index=True):
    conn = db.get_conn()
    try:
        # 先取出 path（用于同步删 KB auto 引用）
        row = conn.execute("SELECT path FROM watch_roots WHERE id=?", (root_id,)).fetchone()
        path = row["path"] if row else None
        if delete_index:
            conn.execute("DELETE FROM file_index WHERE root_id=?", (root_id,))
        conn.execute("DELETE FROM watch_roots WHERE id=?", (root_id,))
        # 级联删 KB：auto 来源 + path 已从 watch_roots 删除的 kb_folder
        if path:
            conn.execute("DELETE FROM kb_folders WHERE path=? AND source='auto'", (path,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def cleanup_orphan_kb_folders():
    """清理孤儿 KB 引用：kb_folders 里 source='auto' 且 path 不在 watch_roots 的。
    启动时 / 每次 rescan 后调一次（防止手动 SQL 删除 watch_roots 漏掉 KB 同步）。"""
    conn = db.get_conn()
    try:
        n = conn.execute("""
            DELETE FROM kb_folders
            WHERE source='auto'
              AND path NOT IN (SELECT path FROM watch_roots)
        """).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def enabled_root_paths():
    conn = db.get_conn()
    try:
        return [r["path"] for r in
                conn.execute("SELECT path FROM watch_roots WHERE enabled=1")]
    finally:
        conn.close()


# ---------- 索引维护 ----------

def _classify(fi, rules):
    hit = rules_mod.classify_by_ext(fi["ext"], rules)
    if hit:
        return hit[0]
    return "其他"


# ---------- 高频路径缓存 ----------
# upsert_file 由 watcher 对每个文件事件调用（一次事件原本要查 5 次库：
# roots + 每根 COUNT + rules + excludes + 写入）。roots/rules/excludes 变化极少，
# 10 秒 TTL 缓存后单个事件只剩一次写入。
_CACHE_TTL = 10.0
_env_cache = {"t": 0.0, "roots": None, "rules": None, "excludes": None}


def _get_env():
    now = time.time()
    c = _env_cache
    if c["t"] and now - c["t"] < _CACHE_TTL:
        return c
    c["roots"] = [(r["id"], r["path"], os.path.normcase(r["path"]))
                  for r in list_roots()]
    c["rules"] = rules_mod.load_user_rules()
    try:
        conn = db.get_conn()
        try:
            c["excludes"] = [os.path.normcase(r["path"]) for r in
                             conn.execute("SELECT path FROM exclude_rules")]
        finally:
            conn.close()
    except Exception:
        c["excludes"] = []
    c["t"] = now
    return c


def _root_id_of(path, root_map):
    """path 属于哪个监控根（最长前缀匹配）。root_map: [(id, path, normpath)]"""
    best_id, best_len = None, -1
    norm = os.path.normcase(os.path.abspath(path))
    for rid, rp, rn in root_map:
        if norm.startswith(rn.rstrip("\\") + os.sep) or norm == rn:
            if len(rn) > best_len:
                best_id, best_len = rid, len(rn)
    return best_id


def _under_skip_dir(path):
    """路径的某级父目录命中跳过名单（应用数据/缓存目录 + 用户自定义排除）则不入索引。"""
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    parts = [p for p in tail.split(os.sep) if p]
    # 最后一级是文件名本身，只检查目录部分
    for part in parts[:-1]:
        if part.lower() in scanner.SKIP_DIRS:
            return True
    # 自定义排除规则（走 TTL 缓存）
    excludes = _get_env()["excludes"]
    if excludes:
        norm = os.path.normcase(os.path.abspath(path))
        for rn in excludes:
            if norm == rn or norm.startswith(rn.rstrip(os.sep) + os.sep):
                return True
    return False


def upsert_file(path):
    """单个文件增量入库（watcher 调用）。返回类别或 None（不存在/临时文件时删除记录）。"""
    path = os.path.abspath(path)
    if is_temp_file(path) or _under_skip_dir(path):
        remove_file(path)
        return None
    if not os.path.exists(path) or not os.path.isfile(path):
        remove_file(path)
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    env = _get_env()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    fi = {"path": path, "name": os.path.basename(path), "ext": ext,
          "size": int(st.st_size or 0), "mtime": float(st.st_mtime or 0.0)}
    cat = _classify(fi, env["rules"])
    rid = _root_id_of(path, env["roots"])
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO file_index(root_id,path,name,ext,category,size,mtime) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id, name=excluded.name, "
            "ext=excluded.ext, category=excluded.category, size=excluded.size, mtime=excluded.mtime",
            (rid, path, fi["name"], ext, cat, fi["size"], fi["mtime"]))
        conn.commit()
    finally:
        conn.close()
    return cat


def remove_file(path):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM file_index WHERE path=?", (os.path.abspath(path),))
        conn.commit()
    finally:
        conn.close()


def remove_under(dir_path):
    conn = db.get_conn()
    try:
        prefix = os.path.abspath(dir_path) + "%"
        conn.execute("DELETE FROM file_index WHERE path LIKE ?", (prefix,))
        conn.commit()
    finally:
        conn.close()


def full_scan(progress_cb=None):
    """全量扫描所有启用根目录。增量：mtime/size 未变的跳过。
    流式处理：边扫边批量写库，不再把全部文件攒进内存（65 万文件时
    旧实现内存峰值 >300MB 且扫描期间无法落库）。
    progress_cb(stage, i, n, detail)。返回统计。"""
    rules = rules_mod.load_user_rules()
    roots = list_roots()
    stats = {"added": 0, "updated": 0, "removed": 0, "total": 0}

    conn = db.get_conn()
    try:
        known = {r["path"]: (r["size"], r["mtime"], r["category"]) for r in
                 conn.execute("SELECT path, size, mtime, category FROM file_index")}
    finally:
        conn.close()

    root_map = [(r["id"], r["path"], os.path.normcase(r["path"])) for r in roots]
    seen = set()
    batch = []
    n_estimate = len(known)

    def _flush():
        if batch:
            _write_batch(batch)
            batch.clear()

    for r in roots:
        if progress_cb:
            progress_cb("scan", 0, 0, f"扫描 {r['label'] or r['path']}")
        for fi in scanner.iter_scan_folder(r["path"], max_files=1_000_000):
            if is_temp_file(fi["path"]):
                continue
            seen.add(fi["path"])
            stats["total"] += 1
            old = known.get(fi["path"])
            cat = _classify(fi, rules)
            changed = (old is None
                       or abs(old[1] - fi["mtime"]) >= 1
                       or old[0] != fi["size"]
                       or old[2] != cat)  # 规则改了也要刷新类别
            if changed:
                batch.append(( _root_id_of(fi["path"], root_map), fi["path"],
                              fi["name"], fi["ext"], cat, fi["size"], fi["mtime"]))
                stats["added" if old is None else "updated"] += 1
                if len(batch) >= 2000:
                    if progress_cb:
                        progress_cb("index", stats["total"], max(n_estimate, stats["total"]),
                                    fi["name"])
                    _flush()

    _flush()

    # 已删除的文件清理（批量删除，避免逐条 commit）
    dead = [p for p in known if p not in seen]
    if progress_cb:
        progress_cb("clean", 0, len(dead), "")
    stats["removed"] = len(dead)
    for i in range(0, len(dead), 2000):
        chunk = dead[i:i + 2000]
        conn = db.get_conn()
        try:
            conn.executemany("DELETE FROM file_index WHERE path=?",
                             [(p,) for p in chunk])
            conn.commit()
        finally:
            conn.close()

    if progress_cb:
        progress_cb("done", stats["total"], stats["total"], "")
    return stats


def _write_batch(batch):
    conn = db.get_conn()
    try:
        conn.executemany(
            "INSERT INTO file_index(root_id,path,name,ext,category,size,mtime) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id, name=excluded.name, "
            "ext=excluded.ext, category=excluded.category, size=excluded.size, mtime=excluded.mtime",
            batch)
        conn.commit()
    finally:
        conn.close()


# ---------- 搜索与浏览 ----------

import re as _re
import time as _time


def parse_query(query):
    """解析搜索语法：ext:pdf,docx | size:>100mb | dm:week | path:Desktop | cat:图片
    返回 (纯文本关键词, {exts, min_size, max_size, days, path_sub, category})。"""
    filters = {"exts": None, "min_size": None, "max_size": None,
               "days": None, "path_sub": None, "category": None}
    if not query:
        return "", filters
    tokens = _re.findall(r'(\w+):([^\s]+)', query)
    rest = query
    for key, val in tokens:
        k = key.lower()
        if k == "ext":
            filters["exts"] = [e.lower().lstrip(".") for e in val.split(",") if e]
            rest = rest.replace(f"{key}:{val}", "", 1)
        elif k == "size":
            m = _re.match(r'^([<>>=]+)\s*([\d.]+)\s*([kmgt]?b?)$', val.lower())
            if m:
                op, num, unit = m.groups()
                mult = {"": 1, "b": 1, "k": 1 << 10, "kb": 1 << 10,
                        "m": 1 << 20, "mb": 1 << 20, "g": 1 << 30, "gb": 1 << 30,
                        "t": 1 << 40, "tb": 1 << 40}.get(unit, 1)
                size = int(float(num) * mult)
                if "<" in op:
                    filters["max_size"] = size
                else:
                    filters["min_size"] = size
                rest = rest.replace(f"{key}:{val}", "", 1)
        elif k == "dm":
            days = {"today": 1, "今天": 1, "yesterday": 2, "昨天": 2,
                    "week": 7, "本周": 7, "month": 30, "本月": 30, "year": 365}.get(val.lower())
            if days:
                filters["days"] = days
                rest = rest.replace(f"{key}:{val}", "", 1)
        elif k == "path":
            filters["path_sub"] = val
            rest = rest.replace(f"{key}:{val}", "", 1)
        elif k in ("cat", "category"):
            filters["category"] = val
            rest = rest.replace(f"{key}:{val}", "", 1)
    return rest.strip(), filters


def _fts_available():
    try:
        conn = db.get_conn()
        try:
            conn.execute("SELECT COUNT(*) c FROM file_fts LIMIT 1").fetchone()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def search(query=None, category=None, ext=None, min_size=None, is_image=None,
           limit=300, offset=0, sort_by="mtime", sort_order="desc", tag_ids=None):
    """文件搜索：支持语法过滤；长关键词走 FTS5，短关键词退回 LIKE。
    sort_by: mtime/name/size/ext；sort_order: asc/desc；tag_ids: 命中任一标签即返回。"""
    text, f = parse_query(query)
    conds, params = [], []
    if f["exts"]:
        marks = ",".join("?" * len(f["exts"]))
        conds.append(f"file_index.ext IN ({marks})")
        params.extend(f["exts"])
    elif ext:
        conds.append("file_index.ext=?")
        params.append(ext.lower())
    if f["min_size"]:
        conds.append("file_index.size>=?")
        params.append(f["min_size"])
    if f["max_size"]:
        conds.append("file_index.size<=?")
        params.append(f["max_size"])
    if f["days"]:
        conds.append("file_index.mtime>=?")
        params.append(_time.time() - f["days"] * 86400)
    if f["path_sub"]:
        conds.append("lower(file_index.path) LIKE ?")
        params.append(f"%{f['path_sub'].lower()}%")
    cat = f["category"] or category
    if cat:
        conds.append("file_index.category=?")
        params.append(cat)
    if min_size:
        conds.append("file_index.size>=?")
        params.append(min_size)
    if is_image:
        marks = ",".join("?" * len(IMAGE_EXTS))
        conds.append(f"file_index.ext IN ({marks})")
        params.extend(sorted(IMAGE_EXTS))
    if tag_ids:
        marks = ",".join("?" * len(tag_ids))
        conds.append(
            "EXISTS(SELECT 1 FROM file_tags ft WHERE ft.path=file_index.path "
            f"AND ft.tag_id IN ({marks}))")
        params.extend(tag_ids)

    join_fts = ""
    if text:
        # ≥3 字符（trigram 最小粒度）走 FTS5 全文；否则 LIKE
        if len(text) >= 3 and _fts_available():
            q = '"' + text.replace('"', '""') + '"'
            join_fts = " JOIN file_fts ON file_fts.rowid = file_index.id AND file_fts MATCH ?"
            fts_params = [q]
        else:
            conds.append("lower(name) LIKE ?")
            fts_params = []
            params.append(f"%{text.lower()}%")
    else:
        fts_params = []

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql_total = f"SELECT COUNT(*) c FROM file_index{join_fts}{where}"
    # 排序：仅允许白名单字段防注入
    _sort_cols = {"mtime": "mtime", "name": "name", "size": "size", "ext": "ext"}
    col = _sort_cols.get(sort_by, "mtime")
    order = "DESC" if (sort_order or "desc").lower() == "desc" else "ASC"
    sql_rows = (f"SELECT file_index.path, file_index.name, file_index.ext, "
                f"file_index.category, file_index.size, file_index.mtime, "
                f"(SELECT GROUP_CONCAT(t.name,'|') FROM file_tags ft "
                f"JOIN tags t ON t.id=ft.tag_id WHERE ft.path=file_index.path) AS tags "
                f"FROM file_index{join_fts}{where} "
                f"ORDER BY file_index.{col} {order} LIMIT ? OFFSET ?")
    conn = db.get_conn()
    try:
        total = conn.execute(sql_total, fts_params + params).fetchone()["c"]
        rows = conn.execute(sql_rows, fts_params + params + [limit, offset]).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}
    finally:
        conn.close()


def category_stats():
    """按类别统计文件数和体积。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) n, SUM(size) s FROM file_index "
            "GROUP BY category ORDER BY n DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def index_stats():
    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM file_index").fetchone()["c"]
        size = conn.execute("SELECT COALESCE(SUM(size),0) s FROM file_index").fetchone()["s"]
        last = conn.execute("SELECT COALESCE(MAX(mtime),0) m FROM file_index").fetchone()["m"]
        return {"files": n, "total_size": size, "newest_mtime": last}
    finally:
        conn.close()
