"""LLM 接入层：本地 Ollama 或 OpenAI 兼容云端 API 二选一，对话与向量化可独立配置。

省钱混搭示例：对话走云端 API（质量好），向量化留本地 bge-m3（免费）。
API 密钥仅保存在本机 SQLite，不经过任何第三方。
"""
import json
import time

import requests

from backend import db
from backend.ollama_client import OllamaClient

# 常见 OpenAI 兼容服务商（填到 /v1 或等价路径为止）
# models: 该服务商 2026-08 在售推荐模型（name 用于自动填充，note 为说明）
API_PRESETS = [
    {"label": "DeepSeek", "value": "https://api.deepseek.com/v1",
     "models": [
         {"name": "deepseek-chat", "note": "V4-Pro 通用（峰谷计价，闲时半价）"},
         {"name": "deepseek-reasoner", "note": "深度推理"},
     ]},
    {"label": "智谱 GLM", "value": "https://open.bigmodel.cn/api/paas/v4",
     "models": [
         {"name": "glm-4.7-flash", "note": "免费 200K，高性价比"},
         {"name": "glm-5.2", "note": "旗舰 1M 上下文"},
         {"name": "glm-5.1", "note": "上代旗舰"},
     ]},
    {"label": "Kimi / Moonshot", "value": "https://api.moonshot.cn/v1",
     "models": [
         {"name": "kimi-k3", "note": "最新旗舰（k2 已下线）"},
         {"name": "moonshot-v1-128k", "note": "长文本 128K"},
     ]},
    {"label": "通义千问", "value": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": [
         {"name": "qwen-plus-latest", "note": "均衡高性价比"},
         {"name": "qwen3-max", "note": "旗舰推理"},
         {"name": "qwen-flash", "note": "轻量低价"},
     ]},
    {"label": "硅基流动", "value": "https://api.siliconflow.cn/v1",
     "models": [
         {"name": "Qwen/Qwen3-235B-A22B-Instruct", "note": "开源大模型免费档"},
         {"name": "deepseek-ai/DeepSeek-V3", "note": "开源通用"},
     ]},
    {"label": "OpenRouter", "value": "https://openrouter.ai/api/v1",
     "models": [
         {"name": "openrouter/auto", "note": "自动路由最佳模型"},
     ]},
    {"label": "OpenAI", "value": "https://api.openai.com/v1",
     "models": [
         {"name": "gpt-5-mini", "note": "经济款 $0.25/$2"},
         {"name": "gpt-5.2", "note": "旗舰 $1.75/$14"},
     ]},
]


def mode():
    return db.get_setting("llm_mode", "ollama") or "ollama"  # ollama | api


def embed_source():
    return db.get_setting("embed_source", "follow") or "follow"  # follow | ollama | api


def api_base():
    return (db.get_setting("api_base", "") or "").strip().rstrip("/")


def api_key():
    return (db.get_setting("api_key", "") or "").strip()


def api_chat_model():
    return (db.get_setting("api_chat_model", "") or "").strip()


def api_embed_model():
    return (db.get_setting("api_embed_model", "") or "").strip()


def _norm_endpoint(base, path):
    if base.endswith(path):
        return base
    return base + path


class OpenAICompatClient:
    """OpenAI 兼容协议客户端（chat/completions + embeddings，支持 SSE 流式与视觉图片）。"""

    def __init__(self, base="", key="", chat_model="", embed_model=""):
        self.base = base or api_base()
        self.key = key or api_key()
        self._chat_model = chat_model or api_chat_model()
        self._embed_model = embed_model or api_embed_model()

    # ---- 基础 ----

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json"}

    def configured(self):
        return bool(self.base and self.key and self._chat_model)

    def chat_model(self):
        return self._chat_model

    def embed_model(self):
        return self._embed_model

    # ---- 对话 ----

    def chat(self, messages, stream_cb=None, options=None, think=False):
        payload = {"model": self._chat_model, "messages": messages,
                   "stream": bool(stream_cb)}
        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "num_predict" in options:
                payload["max_tokens"] = options["num_predict"]
        r = requests.post(_norm_endpoint(self.base, "/chat/completions"),
                          headers=self._headers(), json=payload,
                          stream=bool(stream_cb), timeout=600)
        r.raise_for_status()
        if not stream_cb:
            data = r.json()
            return data["choices"][0]["message"].get("content") or ""
        full = []
        for line in r.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", errors="ignore")
            if not text.startswith("data:"):
                continue
            body = text[5:].strip()
            if body == "[DONE]":
                break
            try:
                delta = json.loads(body)["choices"][0].get("delta", {})
                tok = delta.get("content") or ""
            except Exception:
                tok = ""
            if tok:
                full.append(tok)
                stream_cb(tok)
        return "".join(full)

    # ---- 向量化 ----

    def embed(self, texts, batch=32):
        vectors = []
        for i in range(0, len(texts), batch):
            r = requests.post(_norm_endpoint(self.base, "/embeddings"),
                              headers=self._headers(),
                              json={"model": self._embed_model, "input": texts[i:i + batch]},
                              timeout=600)
            r.raise_for_status()
            data = r.json()["data"]
            vectors.extend(d["embedding"] for d in sorted(data, key=lambda x: x["index"]))
        return vectors

    def embed_one(self, text):
        return self.embed([text])[0]

    # ---- 视觉 ----

    def describe_image(self, image_path, vl_model=None):
        import base64
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp",
                "gif": "gif", "bmp": "bmp"}.get(ext, "jpeg")
        content = [
            {"type": "text", "text":
                "请分析这张图片，严格按以下两部分输出，不要多余内容：\n"
                "描述：用一两句话客观描述图片内容、场景和主体\n"
                "文字：逐字转录图中所有可见文字（含中文/英文/数字），没有文字则写「无」"},
            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
        ]
        answer = self.chat([{"role": "user", "content": content}],
                           options={"temperature": 0.1, "num_predict": 300})
        return _split_desc_ocr(answer)


def _split_desc_ocr(content):
    """把视觉模型输出拆成 (描述, OCR 文字)。"""
    content = (content or "").strip()
    desc, ocr = content, ""
    if "\n" in content:
        lines = content.splitlines()
        desc_lines, ocr_lines, in_ocr = [], [], False
        for line in lines:
            if line.strip().startswith(("文字", "文字：", "文字:")):
                in_ocr = True
                rest = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if rest and not rest.startswith("文字"):
                    ocr_lines.append(rest)
                continue
            (ocr_lines if in_ocr else desc_lines).append(line)
        desc = "\n".join(desc_lines).strip() or content
        ocr = "\n".join(ocr_lines).strip()
    return desc, ocr


# ---------- 工厂与可用性 ----------

def get_chat_client():
    if mode() == "api":
        return OpenAICompatClient()
    return OllamaClient()


def get_embed_client():
    es = embed_source()
    if es == "ollama" or (es == "follow" and mode() == "ollama"):
        return OllamaClient()
    return OpenAICompatClient()


def chat_status():
    """{"ok": bool, "reason": str} —— 对话模型可用性。"""
    if mode() == "api":
        c = OpenAICompatClient()
        if not (api_base() and api_key()):
            return {"ok": False, "reason": "API 地址或密钥未配置"}
        if not c.chat_model():
            return {"ok": False, "reason": "API 对话模型名未填写"}
        return {"ok": True, "reason": "云端 API"}
    st = OllamaClient().status()
    if not st["running"]:
        return {"ok": False, "reason": "Ollama 未运行"}
    if not st["chat_ready"]:
        return {"ok": False, "reason": f"本地模型 {st['chat_model']} 未安装"}
    return {"ok": True, "reason": f"Ollama · {st['chat_model']}"}


def embed_status():
    es = embed_source()
    using_ollama = es == "ollama" or (es == "follow" and mode() == "ollama")
    if using_ollama:
        oc = OllamaClient()
        st = oc.status()
        if not st["running"]:
            return {"ok": False, "reason": "Ollama 未运行"}
        if not st["embed_ready"]:
            return {"ok": False, "reason": "向量模型 bge-m3 未安装"}
        return {"ok": True, "reason": f"本地 · {oc.embed_model()}"}
    c = OpenAICompatClient()
    if not (api_base() and api_key()):
        return {"ok": False, "reason": "API 地址或密钥未配置"}
    if not c.embed_model():
        return {"ok": False, "reason": "云端向量模型名未填写（或改用本地 bge-m3）"}
    return {"ok": True, "reason": f"云端 · {c.embed_model()}"}


def chat_available():
    return chat_status()["ok"]


def embed_available():
    return embed_status()["ok"]


def provider_summary():
    cs, es = chat_status(), embed_status()
    return {"mode": mode(), "embed_source": embed_source(),
            "api_base": api_base(), "has_key": bool(api_key()),
            "api_chat_model": api_chat_model(), "api_embed_model": api_embed_model(),
            "chat": cs, "embed": es,
            "ollama_running": OllamaClient().status()["running"]}


def test_chat():
    """连通性测试：发一句极短请求，返回延迟与回复。"""
    t0 = time.time()
    try:
        client = get_chat_client()
        reply = client.chat([{"role": "user", "content": "回复两个字：已连通"}],
                            options={"temperature": 0, "num_predict": 20}, think=False)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "reply": reply.strip()[:60], "provider": chat_status()["reason"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200],
                "latency_ms": int((time.time() - t0) * 1000)}
