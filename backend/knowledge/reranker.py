"""本地 rerank：bge-reranker-v2-m3 int8 ONNX（CPU 推理），混合检索结果的最后一档精排。

RRF 只看两路的「名次」，rerank 读原文内容打分——对「向量/FTS 都给了名次但
先后不对」的题是最后一档收益。流程：hybrid top-50 → rerank → top-8。

模型文件不进安装包（int8 ~544MB），设置页按需下载/删除（沿用模型管理 UX）；
onnxruntime / tokenizers 缺失或模型未下载或加载失败时整体降级为纯 RRF，
问答与检索不受影响。CPU 对 50 对打分约 1~3s，故默认关闭，设置页开关。
"""
import json
import os
import threading
import time

import requests

from backend import db

# 模型来源：社区 int8 量化单文件版（官方仓库无 ONNX）。镜像优先（国内直连），
# 失败回退官方站
_REPOS = ("arnauld-a/bge-reranker-v2-m3-onnx-quant-v2",)
_BASES = ("https://hf-mirror.com", "https://huggingface.co")
_FILES = {"model": "model_quant.onnx", "tokenizer": "tokenizer.json"}
MAX_LEN = 512
BATCH = 8

_lock = threading.Lock()
_sess = None
_tok = None
_failed = False      # 本进程加载失败过 → 不再反复尝试（每次问答都撞一次太贵）
_load_error = ""
_dl_lock = threading.Lock()
_dl_prog = {"downloading": False, "pct": 0, "done_bytes": 0, "total_bytes": 0,
            "error": "", "finished_at": 0.0}


def model_dir():
    return os.path.join(db.get_data_dir(), "models", "reranker")


def _model_path(name):
    return os.path.join(model_dir(), _FILES[name])


def model_ready():
    return all(os.path.isfile(_model_path(k)) for k in _FILES)


def runtime_available():
    try:
        import onnxruntime  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
        return True
    except ImportError:
        return False


def enabled():
    return db.get_setting("rerank_enabled", "0") == "1"


def set_enabled(on):
    db.set_setting("rerank_enabled", "1" if on else "0")


def active():
    """问答/检索是否实际会走 rerank（开关 + 依赖 + 模型 + 未加载失败）。"""
    return bool(enabled() and model_ready() and runtime_available() and not _failed)


def status():
    """设置页状态卡：运行时 / 模型 / 下载进度 一览（下载跑动中也可廉价刷新）。"""
    st = db.get_setting("rerank_enabled", "0") == "1"
    sizes = 0
    if model_ready():
        sizes = sum(os.path.getsize(_model_path(k)) for k in _FILES)
    return {"enabled": st, "runtime": runtime_available(),
            "downloaded": model_ready(), "active": active(),
            "loaded": _sess is not None, "failed": _failed,
            "error": _load_error, "size_bytes": sizes,
            "model_dir": model_dir(),
            "download": dict(_dl_prog)}


# ---------- 模型管理（下载 / 删除） ----------

def start_download():
    """后台下载模型文件（镜像优先）。已在跑或已就绪时直接返回当前状态。"""
    with _dl_lock:
        if _dl_prog["downloading"]:
            return {"ok": True, "already": True}
        if model_ready():
            return {"ok": True, "ready": True}
        _dl_prog.update({"downloading": True, "pct": 0, "done_bytes": 0,
                         "total_bytes": 0, "error": "", "finished_at": 0.0})
    threading.Thread(target=_download_run, daemon=True,
                     name="fb-rerank-dl").start()
    return {"ok": True}


def _download_run():
    try:
        os.makedirs(model_dir(), exist_ok=True)
        for key, fname in _FILES.items():
            dest = os.path.join(model_dir(), fname + ".part")
            total = done = 0
            r = None
            for base in _BASES:
                for repo in _REPOS:
                    url = f"{base}/{repo}/resolve/main/{fname}"
                    try:
                        r = requests.get(url, stream=True, timeout=(10, 60))
                        r.raise_for_status()
                        total = int(r.headers.get("Content-Length") or 0)
                        break
                    except Exception:
                        r = None
                if r is not None:
                    break
            if r is None:
                raise RuntimeError("所有镜像均不可达")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    _dl_prog.update({"done_bytes": done, "total_bytes": total,
                                     "pct": int(done * 100 / total) if total else 0})
            os.replace(dest, _model_path(key))
        _dl_prog.update({"downloading": False, "pct": 100,
                         "finished_at": time.time()})
    except Exception as e:
        _dl_prog.update({"downloading": False, "error": str(e)[:200],
                         "finished_at": time.time()})


def delete_model():
    """删除模型文件（磁盘回收）；已加载的会话置失败位后重建即可，不需重启。"""
    global _failed
    for key in _FILES:
        try:
            os.remove(_model_path(key))
        except OSError:
            pass
    with _lock:
        _sess = None
        _failed = False
    return {"ok": True}


# ---------- 打分 ----------

def _load():
    """惰性加载 tokenizer + ONNX 会话（首次 rerank 请求时，约 1s）。"""
    global _sess, _tok, _failed, _load_error
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        _tok = Tokenizer.from_file(_model_path("tokenizer"))
        _tok.enable_truncation(max_length=MAX_LEN)
        _tok.enable_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 0  # 0 = 用满物理核
        _sess = ort.InferenceSession(_model_path("model"), so,
                                     providers=["CPUExecutionProvider"])
    except Exception as e:
        _failed = True
        _load_error = str(e)[:200]
        _sess = None


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + pow(2.718281828459045, -x))
    z = pow(2.718281828459045, x)
    return z / (1.0 + z)


def rerank_scores(query, texts):
    """对 (query, text) 对打相关性分，返回与 texts 对齐的 [0,1] 分数列表。

    任何一步不可用/失败返回 None——调用方回退 RRF 原序，问答绝不因此中断。"""
    if not active():
        return None
    with _lock:
        try:
            if _sess is None:
                _load()
            if _sess is None:
                return None
            out = []
            # text 截 4000 字符：tokenizer 还会按 512 token 截，这里只是防止
            # 超长块白费编码时间
            pairs = [(query, (t or "")[:4000]) for t in texts]
            for i in range(0, len(pairs), BATCH):
                enc = _tok.encode_batch(pairs[i:i + BATCH])
                ids = [e.ids for e in enc]
                att = [e.attention_mask for e in enc]
                logits = _sess.run(
                    None,
                    {"input_ids": ids, "attention_mask": att})[0].reshape(-1)
                # float() 强转：numpy 标量进 JSON 序列化会炸（sources.score 要过桥）
                out.extend(float(_sigmoid(x)) for x in logits)
            return out if len(out) == len(texts) else None
        except Exception:
            return None


def config_json():
    """模型目录里 config.json 的内容（设置页展示用，可缺省）。"""
    try:
        with open(os.path.join(model_dir(), "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
