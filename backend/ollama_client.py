"""Ollama 本地模型服务客户端：状态检测、对话（支持流式）、向量化、模型拉取。"""
import json
import time

import requests

from backend import db

DEFAULT_HOST = "http://127.0.0.1:11434"

# 对话模型按内存推荐；embedding 模型固定用 bge-m3（中文效果好、体积小）
CHAT_MODELS = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:14b", "qwen2.5:1.5b"]
EMBED_MODEL = "bge-m3"

# 本机 Ollama 直连 session：trust_env=False 关键——系统代理（HTTP_PROXY/
# 系统级代理软件）会把 127.0.0.1 的请求也转给代理，探测要等满超时甚至失败，
# 用户感知为「连接 Ollama 等半天」。远程主机则继续走 requests 默认（可被代理）。
_direct_session = requests.Session()
_direct_session.trust_env = False


def _session_for(host):
    if "127.0.0.1" in host or "localhost" in host or host.startswith("http://[::1]"):
        return _direct_session
    return requests


class OllamaNotRunning(Exception):
    pass


class OllamaClient:
    def __init__(self, host=None):
        self.host = host or db.get_setting("ollama_host", DEFAULT_HOST).rstrip("/")

    # 状态探测缓存：在线结果 30s TTL；离线结果只缓存 5s——刚启动 Ollama
    # 最多 5s 后应用就能感知（原来 30s，用户感觉"一直连不上"）。
    _status_cache = {}
    # 模型列表缓存（被 chat_model / has_model 共用，避免每次 HTTP /api/tags）
    _models_cache = {}  # {host: (ts, [models] or None)}

    def status(self):
        """返回服务与模型信息（含大小）；未运行时 running=False。"""
        cached = self._status_cache.get(self.host)
        if cached:
            ttl = 30 if cached[1].get("running") else 5
            if time.time() - cached[0] < ttl:
                return cached[1]
        result = self._status_uncached()
        self._status_cache[self.host] = (time.time(), result)
        return result

    def _list_models(self):
        """查询 Ollama 已安装模型列表，30s 缓存（供 chat_model / has_model 共用）。"""
        cached = self._models_cache.get(self.host)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            s = _session_for(self.host)
            r = s.get(f"{self.host}/api/tags", timeout=(2, 3))
            models = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            models = None  # None 表示探测失败（与空列表区分）
        self._models_cache[self.host] = (time.time(), models)
        return models

    def _status_uncached(self):
        """真实探测（不缓存）。"""
        try:
            s = _session_for(self.host)
            r = s.get(f"{self.host}/api/tags", timeout=(2, 3))
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            model_info = {m["name"]: {"size": m.get("size", 0),
                                      "modified": m.get("modified_at", "")}
                          for m in data.get("models", [])}
            version = ""
            try:
                version = s.get(f"{self.host}/api/version", timeout=(2, 2)).json().get("version", "")
            except Exception:
                pass
            chat = self.chat_model()
            chat_ready = self._match_model(chat, models)
            return {"running": True, "version": version, "models": models,
                    "model_info": model_info,
                    "host": self.host,
                    "chat_model": chat,
                    "chat_ready": chat_ready,
                    "embed_ready": self._match_model(self.embed_model(), models)}
        except Exception:
            return {"running": False, "version": "", "models": [], "model_info": {},
                    "host": self.host,
                    "chat_model": self.chat_model(), "chat_ready": False, "embed_ready": False}

    @staticmethod
    def _match_model(target, models):
        """'qwen2.5' 能匹配 'qwen2.5:7b'；':latest' 后缀自动兼容。"""
        t_base = target.split(":")[0]
        for m in models:
            if m == target or m.split(":")[0] == t_base:
                return True
        return False

    def chat_model(self):
        saved = db.get_setting("chat_model")
        if saved:
            return saved
        # 未设置时自动挑一个已安装的对话模型（用 30s 缓存的模型列表，避免每次 HTTP）
        models = self._list_models() or []
        candidates = [m for m in models if "embed" not in m and "bge" not in m]
        for m in candidates:
            if "qwen" in m.split(":")[0].lower():
                return m
        if candidates:
            return sorted(candidates)[0]
        return "qwen2.5:7b"

    def embed_model(self):
        return db.get_setting("embed_model", EMBED_MODEL)

    # ---------- 对话 ----------

    def chat(self, messages, stream_cb=None, options=None, think=False, model=None):
        """同步对话；stream_cb(token) 逐 token 回调，返回完整文本。
        think=False 关闭思考模式（qwen3 系模型思考会吃掉 num_predict 配额）。
        model: 覆盖默认对话模型（VL 模型 OCR 时需要指定为 qwen2.5vl）。"""
        payload = {
            "model": model or self.chat_model(),
            "messages": messages,
            "stream": bool(stream_cb),
            "think": bool(think),
        }
        if options:
            payload["options"] = options
        s = _session_for(self.host)
        try:
            r = s.post(f"{self.host}/api/chat", json=payload,
                       stream=bool(stream_cb), timeout=600)
            r.raise_for_status()
        except requests.HTTPError:
            if "think" in payload:  # 老版本/不支持 think 的模型：去掉参数重试
                payload.pop("think")
                r = s.post(f"{self.host}/api/chat", json=payload,
                           stream=bool(stream_cb), timeout=600)
                r.raise_for_status()
            else:
                raise
        if not stream_cb:
            msg = r.json()["message"]
            content = msg.get("content") or ""
            if not content and msg.get("thinking"):
                content = msg["thinking"]  # 极端情况：只有思考输出
            return content
        full = []
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            msg = data.get("message", {})
            tok = msg.get("content", "")
            if tok:
                full.append(tok)
                stream_cb(tok)
            if data.get("done"):
                break
        return "".join(full)

    # ---------- 向量化 ----------

    def embed(self, texts, batch=32):
        """文本列表 → 向量列表。优先用 /api/embed 批量端点，失败降级逐个。"""
        s = _session_for(self.host)
        vectors = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            try:
                r = s.post(
                    f"{self.host}/api/embed",
                    json={"model": self.embed_model(), "input": chunk},
                    timeout=600,
                )
                r.raise_for_status()
                vectors.extend(r.json()["embeddings"])
            except Exception:
                for t in chunk:  # 老版本 Ollama 无 /api/embed
                    r = s.post(
                        f"{self.host}/api/embeddings",
                        json={"model": self.embed_model(), "prompt": t},
                        timeout=300,
                    )
                    r.raise_for_status()
                    vectors.append(r.json()["embedding"])
        return vectors

    def embed_one(self, text):
        return self.embed([text])[0]

    # ---------- 模型管理 ----------

    def pull(self, model, progress_cb):
        """拉取模型，progress_cb(done_bytes, total_bytes, status)。"""
        s = _session_for(self.host)
        r = s.post(f"{self.host}/api/pull", json={"name": model, "stream": True},
                   stream=True, timeout=None)
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            if "error" in data:
                raise RuntimeError(data["error"])
            progress_cb(
                data.get("completed", 0),
                data.get("total", 0),
                data.get("status", ""),
            )
            if data.get("status") == "success":
                return

    def delete_model(self, model):
        """删除本地模型（释放磁盘空间）。"""
        s = _session_for(self.host)
        r = s.delete(f"{self.host}/api/delete", json={"model": model}, timeout=60)
        if r.status_code != 200:
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text
            return {"ok": False, "error": err}
        return {"ok": True}

    # ---------- 图片理解（多模态，可选） ----------

    def has_model(self, model):
        # 优先用 _list_models 缓存（30s），不走 status()（更重的 JSON）
        models = self._list_models()
        if models is None:
            return False
        return self._match_model(model, models)

    def describe_image(self, image_path, vl_model):
        """用视觉模型生成图片中文描述 + 图中文字转录（用于图片语义搜索）。"""
        import base64
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        s = _session_for(self.host)
        r = s.post(
            f"{self.host}/api/chat",
            json={
                "model": vl_model,
                "messages": [{
                    "role": "user",
                    "content": (
                        "请分析这张图片，严格按以下两部分输出，不要多余内容：\n"
                        "描述：用一两句话客观描述图片内容、场景和主体\n"
                        "文字：逐字转录图中所有可见文字（含中文/英文/数字），没有文字则写「无」"
                    ),
                    "images": [b64],
                }],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 300},
            },
            timeout=300,
        )
        r.raise_for_status()
        msg = r.json()["message"]
        content = (msg.get("content") or "").strip()
        desc, ocr = content, ""
        if "\n" in content:
            lines = content.splitlines()
            desc_lines, ocr_lines, in_ocr = [], [], False
            for line in lines:
                if line.strip().startswith(("文字", "文字：", "文字:")):
                    in_ocr = True
                    rest = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    if rest and rest != "文字" and not rest.startswith("文字"):
                        ocr_lines.append(rest)
                    continue
                (ocr_lines if in_ocr else desc_lines).append(line)
            desc = "\n".join(desc_lines).strip() or content
            ocr = "\n".join(ocr_lines).strip()
        return desc, ocr
