"""Windows 系统内置 OCR 测试（不需要 Ollama/任何模型）。
覆盖：图片中文识别 + CJK 空格归一化、无文字层扫描版 PDF 端到端、
imgsearch 零模型降级（ocr-only 入库可被关键词搜到）。
系统无 OCR 语言包时全部跳过（不算失败）。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db
from backend.knowledge import winocr

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


def make_cn_image(path, lines):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 36)
    im = Image.new("RGB", (720, 60 + 70 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((30, 30 + 70 * i), ln, fill="black", font=font)
    im.save(path)


def make_scanned_pdf(path):
    """生成无文字层的扫描版 PDF：中文渲染成图片再嵌入页面。"""
    import fitz
    from PIL import Image
    tmp_img = path + ".tmp.png"
    make_cn_image(tmp_img, ["扫描件测试订单号", "FB-2026-0823-0042"])
    doc = fitz.open()
    page = doc.new_page(width=720, height=200)
    page.insert_image(fitz.Rect(0, 0, 720, 200), filename=tmp_img)
    doc.save(path)
    doc.close()
    os.remove(tmp_img)


def main():
    if not winocr.available():
        print("SKIP: 系统无 OCR 语言包（设置→语言→选项→光学字符识别）")
        return 0
    check("ocr available + language", bool(winocr.languages()))
    db.init_db()   # 本套件要写 img_index；不自己建表的话只能蹭别的套件建好的库

    tmp = tempfile.mkdtemp(prefix="fb_winocr_")
    try:
        # 1. 图片中文 OCR + 归一化（可被关键词搜索）
        img = os.path.join(tmp, "发票截图.png")
        make_cn_image(img, ["发票号码 20260823-7749", "FileButler OCR Test 001"])
        text = winocr.ocr_file(img)
        check("chinese ocr", "发票号" in text and "7749" in text, repr(text))
        check("latin ocr", "001" in text, repr(text))

        # 2. 无文字层扫描版 PDF 端到端（parse_file 自动走 Windows OCR）
        pdf = os.path.join(tmp, "扫描件.pdf")
        make_scanned_pdf(pdf)
        from backend.knowledge.parsers import parse_file
        t0 = __import__("time").time()
        ptext = parse_file(pdf)
        check("scanned pdf ocr", bool(ptext) and "0042" in ptext, repr((ptext or "")[:80]))
        check("scanned pdf page marker", "[第1页]" in (ptext or ""))

        # 3. imgsearch 零模型降级：ocr-only 入库 → 关键词搜到
        #    （本机 Ollama 装了 VL 时此路径不触发，改为直接验证 upsert/检索闭环）
        st = os.stat(img)
        db.upsert_img(img, st.st_mtime, st.st_size, "", None, ocr=text)
        hits = db.img_keyword_search("7749", k=4)
        check("keyword search hits ocr row",
              any(h["path"] == img for h in hits), str(hits))
        db.remove_img(img)  # 清理：别污染真实库（test_batch2 断言"发票"无结果）
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
