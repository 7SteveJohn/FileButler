"""扫描件 OCR 模型（设置项 ocr_model）测试：
假 Ollama 服务验证决策链（专用模型 → winocr → VL 兜底）、
parse_ver 随设置变化、OTSL 表格转文本。不需要真实模型。"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 必须在导入 backend 前隔离 APPDATA：本套会切换 ocr_model/ollama_host，
# 直跑会写进真实用户库
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_ocrmodel_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db
from backend.knowledge import parsers

PASS = 0
FAIL = 0

# 假服务的模型表与请求记录（用例间改写 + 清 OllamaClient 类级缓存）
FAKE_MODELS = []
CHAT_REQS = []
CHAT_REPLY = ""


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


class FakeOllama(BaseHTTPRequestHandler):
    """最小 Ollama 服务：/api/tags + /api/chat。"""

    def do_GET(self):
        if self.path == "/api/tags":
            self._json(200, {"models": [{"name": m} for m in FAKE_MODELS]})
        else:
            self._json(404, {})

    def do_POST(self):
        if self.path == "/api/chat":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n)) if n else {}
            CHAT_REQS.append(body)
            self._json(200, {"message": {"role": "assistant", "content": CHAT_REPLY}})
        else:
            self._json(404, {})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def make_scanned_pdf(path):
    """无文字层扫描版 PDF：中文渲染成图片再嵌入页面（同 test_winocr）。"""
    import fitz
    from PIL import Image, ImageDraw, ImageFont
    img = path + ".tmp.png"
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 36)
    im = Image.new("RGB", (720, 200), "white")
    d = ImageDraw.Draw(im)
    d.text((30, 30), "扫描件测试订单号 FB-2026-0823-0042", fill="black", font=font)
    im.save(img)
    doc = fitz.open()
    page = doc.new_page(width=720, height=200)
    page.insert_image(fitz.Rect(0, 0, 720, 200), filename=img)
    doc.save(path)
    doc.close()
    os.remove(img)


def bust_caches():
    """OllamaClient 的类级缓存按 host 记 30s，假服务列表变了要清掉。"""
    from backend.ollama_client import OllamaClient
    OllamaClient._models_cache.clear()
    OllamaClient._status_cache.clear()


def patch_winocr(monkey):
    """monkey = {'available': bool, 'ocr_pdf': str}；None 还原。"""
    from backend.knowledge import winocr
    if monkey is None:
        winocr.available = _ORIG_WINOCR["available"]
        winocr.ocr_pdf = _ORIG_WINOCR["ocr_pdf"]
    else:
        calls = []
        winocr.available = lambda: monkey["available"]
        def fake_pdf(path, max_pages=30, dpi=200, page_cb=None):
            calls.append(path)
            return monkey["ocr_pdf"]
        winocr.ocr_pdf = fake_pdf
        monkey["calls"] = calls


_ORIG_WINOCR = {}


def main():
    db.init_db()
    from backend.knowledge import winocr
    _ORIG_WINOCR["available"] = winocr.available
    _ORIG_WINOCR["ocr_pdf"] = winocr.ocr_pdf

    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{srv.server_address[1]}"

    saved = {k: db.get_setting(k, "") for k in ("ocr_model", "ollama_host", "chat_model")}
    tmp = tempfile.mkdtemp(prefix="fb_ocrmodel_")
    try:
        db.set_setting("ollama_host", host)

        # ---------- 1. 设置读写 + parse_ver ----------
        check("ocr_model default empty", parsers.ocr_model() == "")
        check("parse_ver plain when unset", parsers.parse_ver() == parsers.PARSE_VER,
              parsers.parse_ver())
        db.set_setting("ocr_model", "  longwayxu/xiaomi-ocr-0:bf16  ")
        check("ocr_model stripped", parsers.ocr_model() == "longwayxu/xiaomi-ocr-0:bf16")
        check("parse_ver carries model",
              parsers.parse_ver() == parsers.PARSE_VER + "+ocr:longwayxu/xiaomi-ocr-0:bf16",
              parsers.parse_ver())
        db.set_setting("ocr_model", "")
        check("parse_ver back to plain", parsers.parse_ver() == parsers.PARSE_VER)

        # ---------- 2. OTSL → 行文本 ----------
        OTSL = "## 采购明细表\n\n<fcel>商品名称<fcel>数量<nl><fcel>铅笔<fcel>12<nl><fcel>合计<fcel>—<nl>"
        t = parsers._otsl_to_text(OTSL)
        check("otsl rows tsv", "商品名称\t数量" in t and "铅笔\t12" in t and "合计\t—" in t, repr(t))
        check("otsl prose kept", t.startswith("## 采购明细表"), repr(t))
        check("otsl no tags left", "<fcel>" not in t and "<nl>" not in t, repr(t))
        check("plain text passthrough", parsers._otsl_to_text("普通正文，无表格。") == "普通正文，无表格。")
        t2 = parsers._otsl_to_text("<fcel>A<fcel>1<nl><fcel>B<fcel>")
        check("otsl trailing empty cell", t2.splitlines() == ["A\t1", "B\t"], repr(t2))

        # ---------- 3. 决策链：专用模型优先 ----------
        pdf = os.path.join(tmp, "扫描件.pdf")
        make_scanned_pdf(pdf)
        win = {"available": True, "ocr_pdf": "系统OCR文本"}
        patch_winocr(win)
        try:
            # 3a. 配置 + 模型在库 → 走模型，winocr 不动
            db.set_setting("ocr_model", "longwayxu/xiaomi-ocr-0:bf16")
            FAKE_MODELS[:] = ["longwayxu/xiaomi-ocr-0:bf16", "qwen2.5vl:7b"]
            CHAT_REQS.clear()
            globals()["CHAT_REPLY"] = (
                "采购清单\n\n<fcel>商品<fcel>数量<nl><fcel>铅笔<fcel>12<nl>")
            bust_caches()
            out = parsers._ocr_scanned_pdf(pdf)
            check("model path wins over winocr", "铅笔\t12" in (out or ""), repr((out or "")[:80]))
            check("winocr not called when model ready", win["calls"] == [], str(win["calls"]))
            check("chat got model tag", CHAT_REQS and CHAT_REQS[0]["model"] == "longwayxu/xiaomi-ocr-0:bf16",
                  str(CHAT_REQS[:1]))
            check("chat got official prompt", CHAT_REQS and "OTSL" in CHAT_REQS[0]["messages"][0]["content"])
            check("chat sent image", CHAT_REQS and CHAT_REQS[0]["messages"][0].get("images"), "")
            opts = CHAT_REQS[0]["options"] if CHAT_REQS else {}
            check("chat num_ctx explicit", opts.get("num_ctx") == parsers.XIAOMI_OCR_NUM_CTX, str(opts))
            check("chat num_predict", opts.get("num_predict") == parsers.XIAOMI_OCR_NUM_PREDICT, str(opts))

            # 3b. 配置了但模型不在库 → 回退 winocr
            FAKE_MODELS[:] = ["qwen2.5vl:7b"]
            bust_caches()
            out = parsers._ocr_scanned_pdf(pdf)
            check("missing model falls back to winocr", out == "系统OCR文本", repr(out))
            check("winocr called on fallback", len(win["calls"]) == 1, str(win["calls"]))

            # 3c. 未配置 → 直接 winocr（现状链路不变）
            db.set_setting("ocr_model", "")
            win["calls"].clear()
            out = parsers._ocr_scanned_pdf(pdf)
            check("unset goes winocr first", out == "系统OCR文本" and len(win["calls"]) == 1,
                  repr(out))

            # 3d. 未配置 + 系统 OCR 不可用 → VL 兜底（转录提示词、无 num_ctx）
            #     chat_available() 在 ollama 模式要求对话模型就绪，显式指向假库里已有的模型
            db.set_setting("chat_model", "qwen2.5vl:7b")
            FAKE_MODELS[:] = ["qwen2.5vl:7b"]
            CHAT_REQS.clear()
            globals()["CHAT_REPLY"] = "逐字转录内容"
            patch_winocr({"available": False, "ocr_pdf": ""})
            bust_caches()
            out = parsers._ocr_scanned_pdf(pdf)
            check("vl fallback transcribes", "逐字转录内容" in (out or ""), repr(out))
            if CHAT_REQS:
                check("vl fallback uses transcribe prompt",
                      "逐字转录" in CHAT_REQS[0]["messages"][0]["content"],
                      CHAT_REQS[0]["messages"][0]["content"][:60])
                check("vl fallback no num_ctx", "num_ctx" not in CHAT_REQS[0]["options"],
                      str(CHAT_REQS[0]["options"]))

            # 3e. 全都不行 → None
            FAKE_MODELS[:] = []
            patch_winocr({"available": False, "ocr_pdf": ""})
            bust_caches()
            check("all unavailable returns None", parsers._ocr_scanned_pdf(pdf) is None)
        finally:
            patch_winocr(None)

        # ---------- 4. parse_file 端到端（走模型路径，带页码标记） ----------
        db.set_setting("ocr_model", "longwayxu/xiaomi-ocr-0:bf16")
        FAKE_MODELS[:] = ["longwayxu/xiaomi-ocr-0:bf16"]
        CHAT_REQS.clear()
        globals()["CHAT_REPLY"] = "<fcel>商品<fcel>数量<nl><fcel>铅笔<fcel>12<nl>"
        bust_caches()
        out = parsers.parse_file(pdf)
        check("parse_file e2e page marker", (out or "").startswith("[第1页]"), repr((out or "")[:40]))
        check("parse_file e2e otsl converted", "铅笔\t12" in (out or ""), repr(out))
    finally:
        for k, v in saved.items():
            db.set_setting(k, v)
        bust_caches()
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)
