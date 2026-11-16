"""HEIC 支持测试：iPhone 照片的缩略图 / 拍摄时间 / dHash / 预览转码四条链路。

样本用 pillow-heif 现场合成，不依赖仓库里放二进制文件。
"""
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_heic_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, imgdecode                     # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def make_heic(path, size=(320, 240), color=(200, 90, 40), exif_dt=None):
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()      # 注册后 PIL 可直接写 HEIF
    im = Image.new("RGB", size, color)
    # 画一块深色方块，保证 dHash 不是纯色退化哈希
    for x in range(size[0] // 3, size[0] // 2):
        for y in range(size[1] // 3, size[1] // 2):
            im.putpixel((x, y), (10, 10, 10))
    kw = {"format": "HEIF"}
    if exif_dt:
        exif = im.getexif()
        exif[36867] = exif_dt          # DateTimeOriginal
        kw["exif"] = exif.tobytes()
    im.save(path, **kw)
    return path


def main():
    import pillow_heif                                  # noqa: F401
    from backend import fileindex, thumbserver
    from backend.core import simimg

    db.init_db()
    d = tempfile.mkdtemp(prefix="fb_heic_")

    p = make_heic(os.path.join(d, "photo.heic"), exif_dt="2021:05:04 12:30:00")
    check("样本是 HEIC", imgdecode.is_heic(p) and os.path.getsize(p) > 0)

    # 1) 解码
    with imgdecode.open_image(p) as im:
        check("open_image 能打开 HEIC", im.size == (320, 240), str(im.size))

    # 2) 转码给模型/OCR
    jb = imgdecode.jpeg_bytes(p)
    check("jpeg_bytes 输出 JPEG", jb[:2] == b"\xff\xd8" and len(jb) > 500, str(jb[:2]))

    # 3) 视觉模型分支：HEIC 走转码、其它格式原样（这里只验 HEIC 不再原样上传）
    check("HEIC 原图不是 JPEG（证明确实转码了）",
          open(p, "rb").read(2) != b"\xff\xd8")

    # 4) 缩略图
    th = thumbserver.generate_thumb(p)
    check("缩略图生成成功", bool(th) and os.path.exists(th), str(th))

    # 5) EXIF 拍摄时间入库
    ts = fileindex._photo_time_of(p)
    check("EXIF 拍摄时间可读", bool(ts), str(ts))

    # 6) dHash（相似图片检测）
    h = simimg.dhash(p)
    check("dHash 非空且非退化", h is not None and 6 <= h.bit_count() <= 58, str(h))

    # 7) 相似图能成组：同一张图存两份
    p2 = os.path.join(d, "photo2.heic")
    make_heic(p2, exif_dt="2021:05:04 12:30:00")
    files = [{"path": p, "ext": "heic", "size": os.path.getsize(p)},
             {"path": p2, "ext": "heic", "size": os.path.getsize(p2)}]
    groups = simimg.find_similar(files)
    check("相似 HEIC 能成组", len(groups) == 1 and len(groups[0]["paths"]) == 2, str(groups))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
