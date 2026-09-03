"""文件总索引 + watcher + 缩略图服务测试（不需要 Ollama）。"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex
from backend import thumbserver, watcher

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def make_test_root():
    # 沙盒必须放在 AppData 之外（AppData 在扫描跳过名单里）
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".test_tmp")
    os.makedirs(base, exist_ok=True)
    root = tempfile.mkdtemp(prefix="fb_lib_", dir=base)
    sub = os.path.join(root, "项目资料")
    os.makedirs(sub)
    files = {
        "年度报告.pdf": b"%PDF-1.4 " + b"x" * 100,
        "风景照.jpg": bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"jpgdata" * 50,
        "logo.png": bytes([0x89]) + b"PNG\r\n\x1a\n" + b"data" * 50,
        "笔记.txt": b"some notes",
    }
    for rel, content in files.items():
        with open(os.path.join(sub, rel), "wb") as f:
            f.write(content)
    return root


def main():
    db.init_db()
    fileindex.ensure_default_roots()

    # 清理历史测试残留（fb_lib_ / fb_outside_ 前缀的临时根目录）
    for r in fileindex.list_roots(only_enabled=False):
        base = os.path.basename(r["path"])
        if base.startswith(("fb_lib_", "fb_outside_")):
            fileindex.remove_root(r["id"])
            import shutil
            shutil.rmtree(r["path"], ignore_errors=True)

    # 系统用户目录识别
    roots = fileindex.list_roots(only_enabled=False)
    check("default roots detected", len(roots) >= 3, f"{[r['path'] for r in roots]}")

    # 测试根目录入库
    test_root = make_test_root()
    fileindex.add_root(test_root)

    # 1. 全量扫描
    result = fileindex.full_scan()
    check("full scan indexes files", result["total"] >= 4 and result["added"] >= 4, str(result))

    # 2. 分类正确
    r = fileindex.search(query="年度报告")
    check("search by name", r["total"] == 1 and r["items"][0]["category"] == "文档", str(r))
    r = fileindex.search(query="风景照")
    check("image category", r["total"] == 1 and r["items"][0]["category"] == "图片")
    r = fileindex.search(is_image=True)
    check("image filter", r["total"] >= 2, f"{r['total']}")

    # 3. 大小写不敏感
    r = fileindex.search(query="LOGO")
    check("case insensitive", r["total"] == 1)

    # 4. watcher 实时监控
    events = []
    engine = watcher.WatchEngine(on_event=lambda kind, path, cat: events.append((kind, path)))
    n = engine.start([test_root])
    check("watcher started", n == 1)
    time.sleep(1.0)

    new_file = os.path.join(test_root, "新下载的文档.docx")
    with open(new_file, "wb") as f:
        f.write(b"PK\x03\x04" + b"d" * 1000)
    # 模拟缓慢写入（防抖应等待）
    for _ in range(3):
        time.sleep(1.0)
        with open(new_file, "ab") as f:
            f.write(b"more")
    time.sleep(5.0)  # 等待稳定入库
    r = fileindex.search(query="新下载的文档")
    check("watcher picks new file", r["total"] == 1 and r["items"][0]["category"] == "文档", str(r))
    check("watcher emitted event", any(k == "added" and "新下载" in p for k, p in events))

    # 临时文件应被忽略
    tmp_file = os.path.join(test_root, "movie.mkv.crdownload")
    with open(tmp_file, "wb") as f:
        f.write(b"partial")
    time.sleep(5.0)
    r = fileindex.search(query="movie.mkv")
    check("temp file ignored", r["total"] == 0, str(r))

    # 删除事件
    os.remove(new_file)
    time.sleep(3.0)
    r = fileindex.search(query="新下载的文档")
    check("watcher removes deleted", r["total"] == 0)

    engine.stop()

    # 5. 缩略图服务
    ts = thumbserver.ThumbServer()
    port = ts.start()
    check("thumb server port", port > 0)
    # 用 PIL 生成一张真图（假 JPEG 头 PIL 解不开）
    from PIL import Image
    real_jpg = os.path.join(test_root, "项目资料", "风景照.jpg")
    Image.new("RGB", (800, 600), (120, 180, 90)).save(real_jpg, "JPEG")

    import urllib.parse
    url = f"http://127.0.0.1:{port}/thumb?p=" + urllib.parse.quote(
        os.path.join(test_root, "项目资料", "风景照.jpg"))
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = resp.read()
    check("thumbnail generated", resp.status == 200 and len(data) > 100 and data[:2] == b"\xff\xd8",
          f"{len(data)} bytes")
    # 缓存命中（第二次快）
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=15) as resp:
        resp.read()
    check("thumbnail cache hit", time.time() - t0 < 0.5)
    # 白名单外的路径必须 403
    outside = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".test_tmp", "fb_outside")
    os.makedirs(outside, exist_ok=True)
    out_img = os.path.join(outside, "x.jpg")
    with open(out_img, "wb") as f:
        f.write(bytes([0xFF, 0xD8, 0xFF]) + b"j" * 50)
    url2 = f"http://127.0.0.1:{port}/thumb?p=" + urllib.parse.quote(out_img)
    try:
        urllib.request.urlopen(url2, timeout=5)
        check("outside root forbidden", False, "should be 403")
    except urllib.error.HTTPError as e:
        check("outside root forbidden", e.code == 403)
    ts.stop()

    # 清理
    fileindex.remove_root(next(r["id"] for r in fileindex.list_roots(False)
                               if r["path"] == test_root))
    import shutil
    shutil.rmtree(test_root, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
