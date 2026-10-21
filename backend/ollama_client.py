"""Ollama 本地模型服务客户端：状态检测、对话（支持流式）、向量化、模型拉取。"""
import json
import time

import requests

from backend import db

DEFAULT_HOST = "http://127.0.0.1:11434"

# 对话模型按内存推荐；embedding 模型固定用 bge-m3（中文效果好、体积小）
CHAT_MODELS = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:14b", "qwen2.5:1.5b"]
EMBED_MODEL = "bge-m3"

# 幂等请求重试：只对无副作用的幂等调用（embed / 非流式 chat）重试；流式对话
# 已经吐出去的 token 无法撤回，重发会重复输出，故流式一律不重试。
# 4xx（429 除外）是确定性失败（模型名/参数/密钥错），重试也不会成功。
RETRY_TIMES = 2      # 失败后最多再试次数（不含首次请求）
RETRY_BASE = 0.5     # 退避基数（秒）：0.5 → 1.5（×3 指数退避），测试可改小加速

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


def _retryable(exc):
    """连接错误、超时、5xx、429 视为瞬时故障可重试；其余（含非 429 的 4xx）不重试。"""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    resp = getattr(exc, "response", None)
    return (isinstance(exc, requests.HTTPError) and resp is not None
            and (resp.status_code == 429 or resp.status_code >= 500))


def post_retry(post, *args, **kwargs):
    """幂等 POST + raise_for_status：连接错误/超时/5xx/429 最多再试 RETRY_TIMES
    次，按 RETRY_BASE×3^n 指数退避。只对幂等调用使用（embed / 非流式 chat）；
    流式请求不适用——断流重发会把已给用户的 token 再输出一遍。"""
    for attempt in range(RETRY_TIMES + 1):
        try:
            r = post(*args, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt >= RETRY_TIMES or not _retryable(e):
                raise
            time.sleep(RETRY_BASE * (3 ** attempt))


def http_error_detail(e):
    """从 requests.HTTPError 里抠出服务端的错误说明。

    OpenAI 兼容服务 4xx 的 body 通常是 {"error":{"message":...}} 或纯文本，
    只报「400 Bad Request」等于让用户盲猜是模型名、密钥还是参数的问题。
    """
    resp = getattr(e, "response", None)
    if resp is None:
        return str(e)
    detail = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or err)
            elif err is not None:
                detail = str(err)
            else:
                detail = str(data.get("message") or data)
    except Exception:
        try:
            detail = (resp.text or "").strip()
        except Exception:
            detail = ""
    detail = (detail or "").strip()[:300]
    return f"HTTP {resp.status_code}: {detail or getattr(resp, 'reason', '') or '请求被拒绝'}"


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
            r = s.get(f"{self.host}/api/tags", timeout=(0.6, 1.5))  # 本地探测快速失败：未就绪时不拖慢 get_status
            models = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            models = None  # None 表示探测失败（与空列表区分）
        self._models_cache[self.host] = (time.time(), models)
        return models

    def _status_uncached(self):
        """真实探测（不缓存）。"""
        try:
            s = _session_for(self.host)
            r = s.get(f"{self.host}/api/tags", timeout=(0.6, 1.5))  # 本地探测快速失败：未就绪时不拖慢 get_status
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

    def _keep_alive(self):
        """模型常驻时长。Ollama 默认空闲 5 分钟就把模型踢出显存/内存，
        下一次请求要重新载入（大模型几十秒）——这是「连上 Ollama 也慢」的
        主因。这里默认 30m，让对话与向量模型都留在显存里，随叫随到。
        可用设置项 ollama_keep_alive 覆盖（如 "5m"、"1h"、"-1" 永久）。"""
        return db.get_setting("ollama_keep_alive", "30m") or "30m"

    def _num_ctx(self):
        """对话请求显式下发的上下文窗口（token 数）。

        v1.3.0 把问答上下文扩到 1200 字符 × 8 块、再加多轮历史，prompt 可达
        5~8K token，而 Ollama 默认按模型的 num_ctx（2048/4096，随 Modelfile
        而异）**静默截断**——截掉的内容模型「没看到」也不报错，上下文扩容
        等于白做。这里默认下发 8192；设置项 ollama_num_ctx 可覆盖
        （如 "16384"），设 "0" 表示不干预（低配纯 CPU 机器省 KV cache 内存）。
        """
        raw = (db.get_setting("ollama_num_ctx", "8192") or "8192").strip()
        try:
            n = int(raw)
        except ValueError:
            return 8192
        return n if n > 0 else None

    def chat(self, messages, stream_cb=None, options=None, think=False, model=None,
             format=None):
        """同步对话；stream_cb(token) 逐 token 回调，返回完整文本。
        think=False 关闭思考模式（qwen3 系模型思考会吃掉 num_predict 配额）。
        model: 覆盖默认对话模型（VL 模型 OCR 时需要指定为 qwen2.5vl）。
        format: JSON Schema dict 时写入请求 format 字段（Ollama 0.5+ 结构化输出，
        解码层强约束模型只输出合规 JSON）；None 不传该字段，行为不变。"""
        payload = {
            "model": model or self.chat_model(),
            "messages": messages,
            "stream": bool(stream_cb),
            "think": bool(think),
            "keep_alive": self._keep_alive(),
        }
        if format is not None:
            payload["format"] = format
        opts = dict(options) if options else {}
        if "num_ctx" not in opts:
            nctx = self._num_ctx()
            # 带图片的请求（VL OCR 等视觉调用）跳过：视觉模型本就吃显存，
            # 抬高 KV cache 在低配机有 OOM 风险，且其 prompt 短、无截断之虞
            if nctx and not any(
                    isinstance(m, dict) and m.get("images") for m in messages):
                opts["num_ctx"] = nctx
        if opts:
            payload["options"] = opts
        s = _session_for(self.host)
        try:
            if stream_cb:
                # 流式不重试（幂等重试只用于非流式，见 post_retry）：断流重发会
                # 把已回调给 stream_cb 的 token 重复输出
                r = s.post(f"{self.host}/api/chat", json=payload,
                           stream=True, timeout=(5, 600))
                r.raise_for_status()
            else:
                r = post_retry(s.post, f"{self.host}/api/chat", json=payload,
                               timeout=(5, 600))
        except requests.HTTPError as e:
            if "think" in payload and e.response is not None and e.response.status_code == 400:
                # 老版本/不支持 think 的模型：去掉参数重试（发生在任何输出之前，
                # 重发无副作用；这是参数兼容性重试，不走幂等退避计数）
                payload.pop("think")
                r = s.post(f"{self.host}/api/chat", json=payload,
                           stream=bool(stream_cb), timeout=(5, 600))
                try:
                    r.raise_for_status()
                except requests.HTTPError as e2:
                    if "format" in payload and e2.response is not None \
                            and e2.response.status_code == 400:
                        # 更老的服务端连 schema 对象 format 也不认（结构化输出需
                        # Ollama 0.5+）：再去掉 format 重试一次，输出格式约束
                        # 退回提示词 + 解析层正则/json.loads 兜底（与 OpenAI 路径
                        # backend/llm.py 的 response_format 降级同款）。
                        payload.pop("format")
                        r = s.post(f"{self.host}/api/chat", json=payload,
                                   stream=bool(stream_cb), timeout=(5, 600))
                        try:
                            r.raise_for_status()
                        except requests.HTTPError as e3:
                            raise requests.HTTPError(http_error_detail(e3)) from e3
                    else:
                        raise requests.HTTPError(http_error_detail(e2)) from e2
            else:
                raise requests.HTTPError(http_error_detail(e)) from e
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
        """文本列表 → 向量列表。优先用 /api/embed 批量端点，失败降级逐个。
        向量化是幂等调用，连接错误/超时/5xx/429 走 post_retry 退避重试。"""
        s = _session_for(self.host)
        vectors = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            try:
                r = post_retry(
                    s.post,
                    f"{self.host}/api/embed",
                    json={"model": self.embed_model(), "input": chunk,
                          "keep_alive": self._keep_alive()},
                    timeout=(5, 600),
                )
                vectors.extend(r.json()["embeddings"])
            except Exception:
                for t in chunk:  # 老版本 Ollama 无 /api/embed
                    try:
                        r = post_retry(
                            s.post,
                            f"{self.host}/api/embeddings",
                            json={"model": self.embed_model(), "prompt": t,
                                  "keep_alive": self._keep_alive()},
                            timeout=(5, 300),
                        )
                    except requests.HTTPError as e:
                        raise requests.HTTPError(http_error_detail(e)) from e
                    vectors.append(r.json()["embedding"])
        return vectors

    def embed_one(self, text):
        return self.embed([text])[0]

    def preload_models(self):
        """把对话与向量模型提前载入显存/内存并保持常驻（启动后台调用）。
        只带 model + keep_alive 的 /api/generate 请求即「仅加载不生成」。
        顺序：先对话模型（首问零等待），再向量模型。"""
        s = _session_for(self.host)
        for model in dict.fromkeys([self.chat_model(), self.embed_model()]):
            try:
                s.post(f"{self.host}/api/generate",
                       json={"model": model, "keep_alive": self._keep_alive()},
                       timeout=(5, 300))
            except Exception:
                pass  # 预载失败不打扰启动，首次真实请求会照常加载

    # ---------- 模型管理 ----------

    def pull(self, model, progress_cb):
        """拉取模型，progress_cb(done_bytes, total_bytes, status)。"""
        s = _session_for(self.host)
        r = s.post(f"{self.host}/api/pull", json={"name": model, "stream": True},
                   stream=True, timeout=None)
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise requests.HTTPError(http_error_detail(e)) from e
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
                "keep_alive": self._keep_alive(),
            },
            timeout=(5, 300),
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise requests.HTTPError(http_error_detail(e)) from e
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
