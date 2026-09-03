"""分类规则引擎：内置扩展名映射 + 用户自定义规则（存 SQLite，优先级更高）。"""
from backend import db

# 默认类别体系（中文，一级目录名）
CATEGORIES = ["文档", "图片", "视频", "音频", "压缩包", "安装包", "代码", "数据", "字体", "其他"]

# 内置扩展名 → (类别, 子目录)。子目录为空表示直接放类别根目录。
BUILTIN_RULES = {
    # 文档
    "pdf": ("文档", "PDF"), "doc": ("文档", "Word"), "docx": ("文档", "Word"),
    "txt": ("文档", "文本"), "md": ("文档", "Markdown"), "markdown": ("文档", "Markdown"),
    "rtf": ("文档", "文本"), "wps": ("文档", "Word"), "pages": ("文档", "Word"),
    "ppt": ("文档", "PPT"), "pptx": ("文档", "PPT"),
    "xls": ("文档", "Excel"), "xlsx": ("文档", "Excel"), "et": ("文档", "Excel"),
    "epub": ("文档", "电子书"), "mobi": ("文档", "电子书"), "azw3": ("文档", "电子书"),
    "one": ("文档", "笔记"), "xmind": ("文档", "思维导图"),
    # 图片
    "jpg": ("图片", ""), "jpeg": ("图片", ""), "png": ("图片", ""), "gif": ("图片", ""),
    "bmp": ("图片", ""), "webp": ("图片", ""), "heic": ("图片", ""), "tiff": ("图片", ""),
    "svg": ("图片", ""), "ico": ("图片", ""), "psd": ("图片", ""), "raw": ("图片", ""),
    # 视频
    "mp4": ("视频", ""), "avi": ("视频", ""), "mkv": ("视频", ""), "mov": ("视频", ""),
    "wmv": ("视频", ""), "flv": ("视频", ""), "webm": ("视频", ""), "m4v": ("视频", ""),
    "ts": ("视频", ""), "rmvb": ("视频", ""),
    # 音频
    "mp3": ("音频", ""), "wav": ("音频", ""), "flac": ("音频", ""), "aac": ("音频", ""),
    "ogg": ("音频", ""), "wma": ("音频", ""), "m4a": ("音频", ""), "ape": ("音频", ""),
    # 压缩包
    "zip": ("压缩包", ""), "rar": ("压缩包", ""), "7z": ("压缩包", ""), "tar": ("压缩包", ""),
    "gz": ("压缩包", ""), "bz2": ("压缩包", ""), "xz": ("压缩包", ""), "iso": ("压缩包", ""),
    # 安装包
    "exe": ("安装包", ""), "msi": ("安装包", ""), "apk": ("安装包", ""),
    "dmg": ("安装包", ""), "appx": ("安装包", ""), "whl": ("安装包", ""),
    # 代码
    "py": ("代码", "Python"), "ipynb": ("代码", "Python"),
    "js": ("代码", "JavaScript"), "jsx": ("代码", "JavaScript"),
    "ts": ("代码", "TypeScript"), "tsx": ("代码", "TypeScript"),
    "java": ("代码", "Java"), "kt": ("代码", "Java"),
    "c": ("代码", "C-C++"), "h": ("代码", "C-C++"), "cpp": ("代码", "C-C++"),
    "hpp": ("代码", "C-C++"), "cs": ("代码", "C#"), "go": ("代码", "Go"),
    "rs": ("代码", "Rust"), "rb": ("代码", "Ruby"), "php": ("代码", "PHP"),
    "swift": ("代码", "Swift"), "sh": ("代码", "Shell"), "bat": ("代码", "Shell"),
    "ps1": ("代码", "Shell"), "html": ("代码", "Web"), "htm": ("代码", "Web"),
    "css": ("代码", "Web"), "scss": ("代码", "Web"), "vue": ("代码", "Web"),
    # 数据
    "json": ("数据", ""), "xml": ("数据", ""), "yaml": ("数据", ""), "yml": ("数据", ""),
    "sql": ("数据", ""), "db": ("数据", ""), "sqlite": ("数据", ""), "sqlite3": ("数据", ""),
    "parquet": ("数据", ""), "ndjson": ("数据", ""),
    "log": ("数据", "日志"), "ini": ("数据", "配置"), "cfg": ("数据", "配置"),
    "conf": ("数据", "配置"), "toml": ("数据", "配置"),
    # 快捷方式
    "lnk": ("其他", "快捷方式"), "url": ("其他", "快捷方式"),
    # 字体
    "ttf": ("字体", ""), "otf": ("字体", ""), "woff": ("字体", ""), "woff2": ("字体", ""),
    "eot": ("字体", ""),
}


def load_user_rules():
    """读取用户规则并合并（覆盖同扩展名的内置规则）。"""
    merged = dict(BUILTIN_RULES)
    for r in db.get_rules():
        merged[r["pattern"]] = (r["category"], r.get("sub") or "")
    return merged


def classify_by_ext(ext, rules=None):
    """扩展名 → (类别, 子目录)；未知返回 None。"""
    if not ext:
        return None
    rules = rules or load_user_rules()
    return rules.get(ext.lstrip(".").lower())


def all_rules():
    """用于设置页展示：内置 + 用户标记。"""
    user_ids = {r["pattern"] for r in db.get_rules()}
    result = []
    for ext, (cat, sub) in sorted(BUILTIN_RULES.items()):
        result.append({"pattern": ext, "category": cat, "sub": sub, "custom": ext in user_ids})
    return {"categories": CATEGORIES, "rules": result}
