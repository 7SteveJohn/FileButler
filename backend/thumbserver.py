"""缩略图服务：本机回环 HTTP 服务，按需生成图片缩略图并缓存。

安全边界：
- 只绑定 127.0.0.1，随机端口
- 只接受 /thumb?p=<编码路径>，路径必须在监控根目录白名单内
- 只从缓存目录回文件，原始图片仅用于生成缩略图
"""
import hashlib
import io
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from backend import db, fileindex


def thumb_dir():
    """缩略图缓存目录（跟随数据目录，切换后自动生效）。"""
    return os.path.join(db.get_data_dir(), "thumbs")


THUMB_SIZE = 256
MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_gen_lock = threading.Lock()


def _cache_path(src_path, mtime):
    key = hashlib.sha1(f"{os.path.normcase(src_path)}|{int(mtime)}".encode("utf-8")).hexdigest()
    return os.path.join(thumb_dir(), key + ".jpg")


def generate_thumb(src_path):
    """生成缩略图，返回缓存文件路径；失败返回 None。"""
    try:
        st = os.stat(src_path)
        cache = _cache_path(src_path, st.st_mtime)
        if os.path.exists(cache):
            return cache
    except OSError:
        return None

    try:
        from PIL import Image, ImageOps
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)   # EXIF 方向矫正
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            os.makedirs(thumb_dir(), exist_ok=True)
            tmp = cache + ".tmp"
            im.save(tmp, "JPEG", quality=75)
            os.replace(tmp, cache)
        _maybe_cleanup()
        return cache
    except Exception:
        return None


def _system_icon_png(ext):
    """Windows 系统文件类型图标 → PNG bytes（SHGetFileInfo + GetDIBits）。"""
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        import io as _io

        SHGFI_ICON = 0x100
        SHGFI_USEFILEATTRIBUTES = 0x10
        SHGFI_LARGEICON = 0x0

        class SHFILEINFO(ctypes.Structure):
            _fields_ = [("hIcon", ctypes.c_void_p),
                        ("iIcon", ctypes.c_int),
                        ("dwAttributes", wintypes.DWORD),
                        ("szDisplayName", ctypes.c_wchar * 260),
                        ("szTypeName", ctypes.c_wchar * 80)]

        # wintypes 在部分 Python 版本缺少这些结构，自己定义
        class BITMAP(ctypes.Structure):
            _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                        ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                        ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                        ("bmBits", ctypes.c_void_p)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                        ("yHotspot", wintypes.DWORD),
                        ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        sfi = SHFILEINFO()
        ctypes.windll.shell32.SHGetFileInfoW(
            "." + ext, 0, ctypes.byref(sfi), ctypes.sizeof(sfi),
            SHGFI_ICON | SHGFI_USEFILEATTRIBUTES | SHGFI_LARGEICON)
        if not sfi.hIcon:
            return None

        try:
            info = ICONINFO()
            if not ctypes.windll.user32.GetIconInfo(sfi.hIcon, ctypes.byref(info)):
                return None
            # hbmColor 可能是无效句柄（-1），单色图标只有 hbmMask
            INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF
            hbm = info.hbmColor if (info.hbmColor and info.hbmColor != INVALID_HANDLE) else info.hbmMask
            if not hbm or hbm == INVALID_HANDLE:
                return None
            bm = BITMAP()
            if not ctypes.windll.gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
                return None
            w, h = bm.bmWidth, bm.bmHeight
            if w <= 0 or h <= 0:
                return None
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(bmi)
            bmi.biWidth = w
            bmi.biHeight = -h  # 自上而下
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            hdc = ctypes.windll.user32.GetDC(0)
            try:
                ctypes.windll.gdi32.GetDIBits(
                    hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0)
            finally:
                ctypes.windll.user32.ReleaseDC(0, hdc)
            img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
            img.thumbnail((48, 48), Image.LANCZOS)
            out = _io.BytesIO()
            img.save(out, "PNG")
            return out.getvalue()
        finally:
            # 释放系统图标句柄，避免句柄泄漏
            try:
                ctypes.windll.user32.DestroyIcon(sfi.hIcon)
            except Exception:
                pass
    except Exception:
        return None


def _maybe_cleanup():
    """缓存超限时删最旧的文件。"""
    try:
        files = []
        for fn in os.listdir(thumb_dir()):
            fp = os.path.join(thumb_dir(), fn)
            files.append((os.path.getmtime(fp), os.path.getsize(fp), fp))
        total = sum(s for _, s, _ in files)
        if total <= MAX_CACHE_BYTES:
            return
        files.sort()
        for _, size, fp in files:
            if total <= MAX_CACHE_BYTES * 0.8:
                break
            try:
                os.remove(fp)
                total -= size
            except OSError:
                pass
    except OSError:
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "FBThumb/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/thumb":
            self._serve_thumb(parse_qs(parsed.query))
            return
        if parsed.path == "/preview":
            self._serve_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/icon":
            self._serve_file_icon(parse_qs(parsed.query))
            return
        self.send_error(404)

    def _serve_file_icon(self, qs):
        """系统文件类型图标（SHGetFileInfo，无 ffmpeg 依赖），用于列表缩略图列。"""
        ext = (qs.get("ext", [""])[0] or "bin").lstrip(".").lower() or "bin"
        key = hashlib.sha1(f"icon|{ext}".encode("utf-8")).hexdigest() + ".png"
        cache = os.path.join(thumb_dir(), key)
        if not os.path.exists(cache):
            png = _system_icon_png(ext)
            if not png:
                self.send_error(404)
                return
            try:
                os.makedirs(thumb_dir(), exist_ok=True)
                with open(cache, "wb") as f:
                    f.write(png)
            except OSError:
                pass
        try:
            with open(cache, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(500)

    def _serve_preview(self, qs):
        """大图/文档预览：图片与 PDF，同样受监控目录白名单约束。"""
        src = unquote(qs.get("p", [""])[0])
        if not src or not self._allowed(src):
            self.send_error(403)
            return
        ext = os.path.splitext(src)[1].lstrip(".").lower()
        ctype = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
            "svg": "image/svg+xml", "ico": "image/x-icon",
            "pdf": "application/pdf",  # 内置预览用（WebView2 原生渲染）
        }.get(ext)
        if not ctype:
            self.send_error(403)
            return
        try:
            size = os.path.getsize(src)
            if size > 100 * 1024 * 1024:
                self.send_error(413)
                return
            with open(src, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(404)

    def _serve_thumb(self, qs):
        src = unquote(qs.get("p", [""])[0])
        if not src or not self._allowed(src):
            self.send_error(403)
            return
        cache = generate_thumb(src)
        if not cache or not os.path.exists(cache):
            self.send_error(404)
            return
        try:
            with open(cache, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(500)

    def _allowed(self, src):
        norm = os.path.normcase(os.path.abspath(src))
        for root in fileindex.enabled_root_paths():
            rn = os.path.normcase(os.path.abspath(root)).rstrip("\\")
            if norm.startswith(rn + os.sep):
                return True
        return False

    def log_message(self, *args):
        pass  # 静默，不刷日志


class ThumbServer:
    def __init__(self):
        self.httpd = None
        self.port = None

    def start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True,
                         name="fb-thumbserver").start()
        return self.port

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd = None
