"""模型推荐：自动探测本机配置（内存/显存），推荐本地 Ollama 模型与云端 API 模型。

本地推荐基于 Qwen3 系列（2026 主流开源模型，中文/代码/推理均衡，Ollama 一键拉取）：
  qwen3:4b   ~3GB   低配/纯 CPU
  qwen3:8b   ~6GB   8GB 显存或 16GB 内存日常首选
  qwen3:14b  ~10GB  16GB 显存甜点
  qwen3:32b  ~22GB  24GB 显存旗舰（约等于 qwen2.5-72B 水平）
显存优先（GPU 推理流畅）；无独显时按内存给 CPU 档（避免推荐跑不动的模型）。

云端模型名按各服务商 2026-08 在售口径整理（DeepSeek V4 / GLM-5.2 / Kimi K3 / Qwen3-Max / GPT-5.x）。
"""
import shutil
import subprocess

# Windows 下隐藏子进程窗口（避免弹 cmd 黑窗）
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


# ---------- 本地模型分级（按显存） ----------

LOCAL_GPU_TIERS = [
    {"model": "qwen3:32b", "size_gb": 22,
     "min_vram": 20, "min_ram": 32,
     "note": "旗舰：24GB 显存可跑，性能约等于 qwen2.5-72B"},
    {"model": "qwen3:14b", "size_gb": 10,
     "min_vram": 12, "min_ram": 16,
     "note": "16GB 显存甜点，推理/写作/代码均衡"},
    {"model": "qwen3:8b", "size_gb": 6,
     "min_vram": 8, "min_ram": 16,
     "note": "日常首选：8GB 显存即可流畅，超过 qwen2.5-14B"},
    {"model": "qwen3:4b", "size_gb": 3,
     "min_vram": 4, "min_ram": 8,
     "note": "入门：低显存机器也能跑"},
]

# 无独显时按内存给 CPU 档（CPU 推理大模型很慢，最多推荐 8B）
LOCAL_CPU_TIERS = [
    {"model": "qwen3:8b", "size_gb": 6, "min_ram": 16,
     "note": "CPU 推理上限，16GB+ 内存可用（约 5 token/s）"},
    {"model": "qwen3:4b", "size_gb": 3, "min_ram": 8,
     "note": "低配 CPU 也能流畅，优先保证可用性"},
]

# 视觉模型（图片语义搜索/OCR）——保留原推荐并备注升级
VL_MODEL = "qwen2.5vl:7b"
VL_MODEL_NEXT = "qwen3-vl:7b"  # Ollama 标签待确认，暂不切换

EMBED_MODEL = "bge-m3"


# ---------- 云端 API 模型推荐（按服务商 base url） ----------

CLOUD_RECOMMEND = {
    "https://api.deepseek.com/v1": {
        "recommend": "deepseek-chat",
        "models": [
            {"name": "deepseek-chat", "note": "V4-Pro 通用对话（2026-08 起峰谷计价，闲时半价）"},
            {"name": "deepseek-reasoner", "note": "深度推理（思考强度 low/high/max）"},
        ],
    },
    "https://open.bigmodel.cn/api/paas/v4": {
        "recommend": "glm-4.7-flash",
        "models": [
            {"name": "glm-4.7-flash", "note": "免费 200K 上下文，高性价比"},
            {"name": "glm-5.2", "note": "旗舰：1M 上下文，¥8/¥28 每百万 token"},
            {"name": "glm-5.1", "note": "上代旗舰，200K 上下文"},
        ],
    },
    "https://api.moonshot.cn/v1": {
        "recommend": "kimi-k3",
        "models": [
            {"name": "kimi-k3", "note": "最新旗舰（k2 系列已于 2026-05 下线）"},
            {"name": "moonshot-v1-128k", "note": "长文本 128K 稳定款"},
        ],
    },
    "https://dashscope.aliyuncs.com/compatible-mode/v1": {
        "recommend": "qwen-plus-latest",
        "models": [
            {"name": "qwen-plus-latest", "note": "均衡高性价比（qwen3.7-plus 快照）"},
            {"name": "qwen3-max", "note": "旗舰：复杂推理与 Agent 任务"},
            {"name": "qwen-flash", "note": "轻量低价，高频简单任务"},
        ],
    },
    "https://api.siliconflow.cn/v1": {
        "recommend": "Qwen/Qwen3-235B-A22B-Instruct",
        "models": [
            {"name": "Qwen/Qwen3-235B-A22B-Instruct", "note": "开源大模型免费档"},
            {"name": "deepseek-ai/DeepSeek-V3", "note": "开源通用（V4 开源前）"},
        ],
    },
    "https://openrouter.ai/api/v1": {
        "recommend": "openrouter/auto",
        "models": [
            {"name": "openrouter/auto", "note": "自动路由到当前最佳模型"},
        ],
    },
    "https://api.openai.com/v1": {
        "recommend": "gpt-5-mini",
        "models": [
            {"name": "gpt-5-mini", "note": "经济款：$0.25/$2 每百万 token"},
            {"name": "gpt-5.2", "note": "旗舰：$1.75/$14 每百万 token"},
        ],
    },
}


# ---------- 硬件探测 ----------

def detect_ram_gb():
    """物理内存（GB）。"""
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        return mem.ullTotalPhys / (1 << 30)
    except Exception:
        return 8.0


def detect_vram_gb():
    """NVIDIA 显存（GB）；无 nvidia-smi 返回 0（视为无独显）。"""
    try:
        if not shutil.which("nvidia-smi"):
            return 0.0
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            timeout=8, text=True, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        line = out.strip().splitlines()[0] if out.strip() else "0"
        return float(line) / 1024.0
    except Exception:
        return 0.0


def detect_config():
    ram = detect_ram_gb()
    vram = detect_vram_gb()
    return {"ram_gb": round(ram, 1), "vram_gb": round(vram, 1),
            "gpu": vram >= 2.0,
            "gpu_name": _gpu_name()}


def _gpu_name():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            timeout=8, text=True, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        return out.strip().splitlines()[0].strip() if out.strip() else ""
    except Exception:
        return ""


def recommend_local(ram_gb=None, vram_gb=None):
    """按显存优先、内存兜底给出本地对话模型推荐。"""
    if ram_gb is None or vram_gb is None:
        cfg = detect_config()
        ram_gb, vram_gb = cfg["ram_gb"], cfg["vram_gb"]
    if vram_gb >= 4.0:
        for tier in LOCAL_GPU_TIERS:
            if vram_gb >= tier["min_vram"]:
                return {"model": tier["model"], "size_gb": tier["size_gb"],
                        "note": tier["note"], "gpu": True,
                        "reason": f"检测到显存约 {vram_gb:.0f}GB，可在 GPU 上流畅运行"}
    for tier in LOCAL_CPU_TIERS:
        if ram_gb >= tier["min_ram"]:
            return {"model": tier["model"], "size_gb": tier["size_gb"],
                    "note": tier["note"], "gpu": False,
                    "reason": f"未检测到独立显卡，按 {ram_gb:.0f}GB 内存推荐（CPU 推理）"}
    tier = LOCAL_CPU_TIERS[-1]
    return {"model": tier["model"], "size_gb": tier["size_gb"],
            "note": tier["note"], "gpu": False,
            "reason": f"配置较低（{ram_gb:.0f}GB 内存），推荐最小可用模型"}


def cloud_recommend_for(base_url):
    """按服务商地址取推荐模型名。"""
    if not base_url:
        return ""
    for url, cfg in CLOUD_RECOMMEND.items():
        if base_url.rstrip("/").startswith(url.rstrip("/")):
            return cfg["recommend"]
    return ""
