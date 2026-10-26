"""状态文案守卫：后端给出的 code 必须在两个语言包里都有对应条目。

模型状态 / 推荐理由以前是后端拼好的中文句子，英文界面直接漏出中文。
现在后端只负责给 code（+ args），文案在 locales 里。这个测试用一条
正则把 code 抓出来，跟语言包对一遍——以后加分支忘了补文案会立刻红。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import llm, model_rec  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


ZH = read("frontend", "src", "locales", "zh-CN.js")
EN = read("frontend", "src", "locales", "en.js")


def codes_in(source):
    return set(re.findall(r'"code":\s*"([a-z_]+)"', source))


def reason_codes_in(source):
    return set(re.findall(r'"reason_code":\s*"([a-z_]+)"', source))


# ---------- 后端两个函数每个分支都带 code ----------
def test_chat_status_codes():
    """mock 掉各分支，确认都带 code，且 reason 仍保留（错误对象还在用）。"""
    orig_mode, orig_base, orig_local, orig_status = (
        llm.mode, llm.api_base, llm.is_local_base, llm.OllamaClient)

    class FakeOllama:
        def __init__(self):
            self.ready = {"running": True, "chat_ready": False, "embed_ready": False,
                          "chat_model": "qwen3:8b"}
            self._embed = "bge-m3"

        def status(self):
            return dict(self.ready)

        def embed_model(self):
            return self._embed

    try:
        llm.mode = lambda: "api"
        llm.api_base = lambda: ""
        r = llm.chat_status()
        check("api 未填地址 → api_no_base", r["code"] == "api_no_base" and not r["ok"]
              and bool(r["reason"]), r)

        llm.api_base = lambda: "http://127.0.0.1:11434/v1"
        llm.is_local_base = lambda b: True

        class FakeClient:
            def chat_model(self):
                return "qwen3:8b"

            def embed_model(self):
                return "bge-m3"

        orig_client = llm.OpenAICompatClient
        llm.OpenAICompatClient = FakeClient
        r = llm.chat_status()
        check("api 本机 → api_local", r["code"] == "api_local" and r["ok"], r)

        llm.api_base = lambda: "https://api.deepseek.com/v1"
        llm.is_local_base = lambda b: False
        r = llm.chat_status()
        check("api 云端 → api_cloud", r["code"] == "api_cloud" and r["ok"], r)
        llm.OpenAICompatClient = orig_client

        llm.mode = lambda: "ollama"
        fake = FakeOllama()
        llm.OllamaClient = lambda: fake
        r = llm.chat_status()
        check("ollama 未就绪 → model_missing",
              r["code"] == "model_missing" and r["args"]["model"] == "qwen3:8b", r)

        fake.ready["chat_ready"] = True
        r = llm.chat_status()
        check("ollama 就绪 → ollama_ok", r["code"] == "ollama_ok" and r["ok"], r)

        fake.ready["running"] = False
        r = llm.chat_status()
        check("ollama 没跑 → ollama_down", r["code"] == "ollama_down", r)

        fake.ready = {"running": True, "chat_ready": True,
                      "embed_ready": False, "chat_model": "qwen3:8b"}
        r = llm.embed_status()
        check("向量模型缺失 → embed_missing", r["code"] == "embed_missing", r)

        fake.ready["embed_ready"] = True
        r = llm.embed_status()
        check("向量模型就绪 → embed_ok",
              r["code"] == "embed_ok" and r["args"]["model"] == "bge-m3", r)
    finally:
        llm.mode, llm.api_base, llm.is_local_base, llm.OllamaClient = (
            orig_mode, orig_base, orig_local, orig_status)


def test_recommend_tier_codes():
    r = model_rec.recommend_local(ram_gb=32, vram_gb=24)
    check("大显存 → gpu 档 + rec_gpu",
          r["reason_code"] == "rec_gpu" and r["tier"] == "gpu32" and r["gpu"], r)
    r = model_rec.recommend_local(ram_gb=16, vram_gb=0)
    check("无独显 → cpu 档 + rec_cpu",
          r["reason_code"] == "rec_cpu" and r["tier"] == "cpu8" and not r["gpu"], r)
    r = model_rec.recommend_local(ram_gb=4, vram_gb=0)
    check("低配 → rec_low", r["reason_code"] == "rec_low" and r["tier"] == "cpu4", r)


def test_locale_keys():
    llm_codes = codes_in(read("backend", "llm.py"))
    check("llm.py 抓到 code", len(llm_codes) >= 8, llm_codes)
    for c in sorted(llm_codes):
        check(f"zh 有 status.{c}", re.search(rf"\b{c}:\s*'", ZH) is not None)
        check(f"en 有 status.{c}", re.search(rf"\b{c}:\s*'", EN) is not None)

    rec_codes = reason_codes_in(read("backend", "model_rec.py"))
    for c in sorted(rec_codes):
        check(f"zh 有 settings.{c}", re.search(rf"\b{c}:\s*'", ZH) is not None)
        check(f"en 有 settings.{c}", re.search(rf"\b{c}:\s*'", EN) is not None)

    for tier in [t["id"] for t in model_rec.LOCAL_GPU_TIERS + model_rec.LOCAL_CPU_TIERS]:
        check(f"zh 有 tier.{tier}", re.search(rf"\b{tier}:\s*'", ZH) is not None)
        check(f"en 有 tier.{tier}", re.search(rf"\b{tier}:\s*'", EN) is not None)

    # 分类 / 子分类：中文原值 → 英文键，两边都要有
    for key in ("doc", "image", "video", "audio", "archive",
                "installer", "code", "data", "font", "other"):
        check(f"zh 有 cat.{key}", re.search(rf"\b{key}:\s*'", ZH) is not None)
        check(f"en 有 cat.{key}", re.search(rf"\b{key}:\s*'", EN) is not None)
    for key in ("shortcut", "map", "text", "log", "ebook", "note", "config"):
        check(f"zh 有 sub.{key}", re.search(rf"\b{key}:\s*'", ZH) is not None)
        check(f"en 有 sub.{key}", re.search(rf"\b{key}:\s*'", EN) is not None)


test_chat_status_codes()
test_recommend_tier_codes()
test_locale_keys()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
