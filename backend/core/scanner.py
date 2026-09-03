"""目录扫描：递归遍历、元数据采集、跳过系统目录。"""
import os

# 跳过的目录名（Windows 系统目录、隐藏目录、常见工具缓存、应用数据目录）
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "appdata", "$windows.~bt",
    "windows.old", "node_modules", ".git", ".svn", ".hg", "__pycache__",
    ".venv", "venv", ".idea", ".vscode", ".cache", "dist", "build",
    "filebutler",  # 应用自身数据目录
    # 应用自管理的数据目录（在文档/桌面下常见，编入索引只会产生噪音）
    "xwechat_files", "wechat files", "tencent files", "qq files", "wexin files",
    "my games", "overwatch", "battle.net", "ubisoft game launcher",
    "rockstar games", "electronic arts", "stardewvalley", "mods",
    "nvidia", "intelgraphicsprofiles", "amd",
}

# 二进制内容魔数（供 AI 分类参考）
MAGIC = [
    (b"\x89PNG", "PNG 图片"), (b"\xff\xd8\xff", "JPEG 图片"), (b"GIF8", "GIF 图片"),
    (b"%PDF", "PDF 文档"), (b"PK\x03\x04", "ZIP 系压缩/Office 文档"),
    (b"\xd0\xcf\x11\xe0", "MS Office 旧版文档"), (b"\x1f\x8b", "GZIP 压缩"),
    (b"Rar!", "RAR 压缩包"), (b"7z\xbc\xaf", "7Z 压缩包"), (b"MZ", "Windows 可执行文件"),
    (b"ID3", "MP3 音频"), (b"ftyp", "视频容器(mp4/mov)"), (b"\x00\x00\x01", "MPEG 视频"),
    (b"OggS", "OGG 音频"), (b"FLAC", "FLAC 音频"), (b"SQLit", "SQLite 数据库"),
]


def _load_excludes():
    """自定义排除规则（路径前缀匹配）。每次扫描只查一次库。"""
    try:
        from backend import db as _db
        conn = _db.get_conn()
        try:
            return [os.path.normcase(r["path"]) for r in
                    conn.execute("SELECT path FROM exclude_rules").fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _path_excluded(path, excludes):
    """路径是否命中排除前缀。path 需已 normcase。"""
    for rn in excludes:
        if path == rn or path.startswith(rn.rstrip(os.sep) + os.sep):
            return True
    return False


def _skip(name, path, skip_hidden=True, excludes=None):
    lname = name.lower()
    if lname in SKIP_DIRS:
        return True
    if skip_hidden and name.startswith(".") and lname not in (".gitignore",):
        return True
    if excludes:
        norm = os.path.normcase(os.path.abspath(path))
        if _path_excluded(norm, excludes):
            return True
    return False


def _is_reparse(entry):
    """符号链接 / 目录联接（junction）。不索引也不跟随：junction 会被误当
    普通文件入库（如 C:\\Users\\All Users），跟随则可能循环嵌套。"""
    try:
        if entry.is_symlink():
            return True
        ij = getattr(entry, "is_junction", None)  # Python 3.13+
        return bool(ij and ij())
    except OSError:
        return False


def _iter_files(root, excludes, budget):
    """os.scandir 递归生成文件信息。Windows 上 DirEntry.stat 的元数据随目录
    枚举免费返回（PEP 471），避免 os.walk + os.stat 的逐文件二次系统调用。
    budget=[剩余数量] 跨子树共享的上限。"""
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except OSError:
        return
    dirs = []
    for entry in entries:
        if budget[0] <= 0:
            return
        if _is_reparse(entry):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                dirs.append(entry)
                continue
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        ext = os.path.splitext(entry.name)[1].lstrip(".").lower()
        budget[0] -= 1
        yield {
            "path": entry.path,
            "name": entry.name,
            "ext": ext,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "rel": os.path.relpath(entry.path, root),
        }
    for d in dirs:
        if budget[0] <= 0:
            return
        if _skip(d.name, d.path, excludes=excludes):
            continue
        yield from _iter_files(d.path, excludes, budget)


def scan_folder(root, progress_cb=None, max_files=200000):
    """递归扫描，返回文件信息列表。progress_cb(scanned, current_dir)。
    支持迭代器形式的 iter_scan_folder，供全量扫描流式处理。"""
    files = []
    for fi in iter_scan_folder(root, progress_cb=progress_cb, max_files=max_files):
        files.append(fi)
    return files


def iter_scan_folder(root, progress_cb=None, max_files=200000):
    """scan_folder 的生成器版本：边扫边产出，不需要把全部文件攒进内存。"""
    root = os.path.abspath(root)
    excludes = _load_excludes()
    budget = [max_files]
    n = 0
    last_dir = [None]

    for fi in _iter_files(root, excludes, budget):
        d = os.path.dirname(fi["path"])
        if d != last_dir[0]:
            last_dir[0] = d
            if progress_cb:
                progress_cb(n, d)
        n += 1
        yield fi


def peek_file(path, ext, max_bytes=4096):
    """读取文件头部，返回 (内容摘要文本, 魔数描述)。给 AI 分类的轻量线索。"""
    magic_desc = ""
    peek = ""
    try:
        with open(path, "rb") as f:
            head = f.read(max_bytes)
        for sig, desc in MAGIC:
            if head.startswith(sig):
                magic_desc = desc
                break
        if ext in TEXT_EXTS:
            peek = head.decode("utf-8", errors="ignore")[:400]
    except OSError:
        pass
    return peek, magic_desc


# 可按纯文本读取的扩展名
TEXT_EXTS = {
    "txt", "md", "markdown", "log", "ini", "cfg", "conf", "csv", "tsv", "json",
    "xml", "yaml", "yml", "toml", "py", "js", "ts", "jsx", "tsx", "html", "htm",
    "css", "scss", "java", "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php",
    "swift", "sh", "bat", "ps1", "sql", "vue",
}

# 临时/不完整文件（下载中、Office 锁文件等），索引时忽略
TEMP_EXTS = {"crdownload", "part", "tmp", "partial", "download", "opdownload"}
TEMP_PREFIXES = ("~$", ".~", "tmp_", "._")
TEMP_SUFFIXES = (".tmp", ".bak~")


def is_temp_file(path):
    """下载中的临时文件 / Office 锁文件，不应进入索引。"""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if ext in TEMP_EXTS:
        return True
    if name.startswith(TEMP_PREFIXES):
        return True
    if name.endswith(TEMP_SUFFIXES):
        return True
    return False
