"""LLM 接入层加固测试：结构化输出（format / response_format 降级）+ 幂等重试。

覆盖（全部打桩 HTTP 层，不依赖真实模型服务）：
  1. Ollama chat(format=schema) 写入 payload["format"]；默认不写（向后兼容）
  2. Ollama 服务端 400 时的参数降级链：先去 think、仍 400 再去 format 各重试
     一次（老服务端不认 schema 对象 format，结构化输出需 Ollama 0.5+）
  3. OpenAI 兼容 chat(format=schema) 发 json_schema response_format；
     服务端 400 不支持时自动去掉该字段重试一次；流式原样透传不降级
  4. 幂等重试：embed / 非流式 chat 对 5xx、429、连接错误最多退避重试
     RETRY_TIMES 次（退避基数 ×3 指数），4xx（非 429）不重试
  5. 流式 chat 不重试（断流重发会重复输出）
  6. classifier 调用带 JSON Schema（category 枚举），正则+json.loads 兜底解析仍工作
  7. num_ctx 显式下发：文本请求默认 8192（防 Ollama 默认窗口静默截断）、
     调用方自带优先、设置项可覆盖、"0" 关闭、带图片的请求跳过、非法值回落、
     注入不污染调用方的 options dict

运行：.venv/Scripts/python tests/test_llm_robust.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_llmrobust_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, llm
from backend import ollama_client
from backend.core import classifier, rules as rules_mod

PASS = 0
FAIL = 0

# 请求计数与收到的请求体（按测试重置）
REC = {"ol_chat": 0, "ol_embed": 0, "ol_chat_bodies": [],
       "oa_chat": 0, "oa_embed": 0, "oa_chat_bodies": []}
# 失败注入：>0 时接下来 N 次请求返回 fail_code
STATE = {"ol_chat_fail": 0, "ol_embed_fail": 0,
         "oa_chat_fail": 0, "oa_embed_fail": 0, "oa_rf_fail": 0,
         "ol_fmt_fail": 0,
         "fail_code": 500, "chat_content": "好的",
         "stream_tokens": ["你", "好"]}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def reset_state():
    for k in ("ol_chat_fail", "ol_embed_fail", "oa_chat_fail",
              "oa_embed_fail", "oa_rf_fail", "ol_fmt_fail"):
        STATE[k] = 0
    STATE["fail_code"] = 500
    REC.update({"ol_chat": 0, "ol_embed": 0, "ol_chat_bodies": [],
                "oa_chat": 0, "oa_embed": 0, "oa_chat_bodies": []})


class _TimeStub:
    """顶掉 ollama_client.time：sleep 只记账不真等，time 委托真时钟。"""
    def __init__(self):
        self.delays = []

    def sleep(self, s):
        self.delays.append(s)

    def time(self):
        return time.time()


def fast_retry():
    """退避基数调小 + sleep 记账，返回 _TimeStub（用完需还原）。"""
    old_base, old_time = ollama_client.RETRY_BASE, ollama_client.time
    ollama_client.RETRY_BASE = 0.001
    stub = _TimeStub()
    ollama_client.time = stub
    return stub, (old_base, old_time)


def restore_retry(stub, saved):
    ollama_client.RETRY_BASE, ollama_client.time = saved


class FakeOllama(BaseHTTPRequestHandler):
    """最小 Ollama 服务：/api/chat（NDJSON 流式/非流式）+ /api/embed。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        if self.path.endswith("/api/chat"):
            REC["ol_chat"] += 1
            REC["ol_chat_bodies"].append(body)
            if body.get("format") and STATE["ol_fmt_fail"] > 0:
                # 只在带 format 时拒绝 → 模拟不支持结构化输出的老服务端（<0.5）
                STATE["ol_fmt_fail"] -= 1
                self._json(400, {"error": "format unsupported"})
                return
            if STATE["ol_chat_fail"] > 0:
                STATE["ol_chat_fail"] -= 1
                self._json(STATE["fail_code"], {"error": "boom"})
                return
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for tok in STATE["stream_tokens"]:
                    self.wfile.write(json.dumps(
                        {"message": {"content": tok}, "done": False}).encode() + b"\n")
                self.wfile.write(json.dumps(
                    {"message": {"content": ""}, "done": True}).encode() + b"\n")
            else:
                self._json(200, {"message": {"content": STATE["chat_content"]}})
        elif self.path.endswith("/api/embed"):
            REC["ol_embed"] += 1
            if STATE["ol_embed_fail"] > 0:
                STATE["ol_embed_fail"] -= 1
                self._json(STATE["fail_code"], {"error": "boom"})
                return
            inp = body.get("input") or []
            self._json(200, {"embeddings": [[float(len(t)), 0.5] for t in inp]})
        else:
            self._json(404, {"error": "no route"})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class FakeOpenAI(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容服务：chat/completions（SSE 流式/非流式）+ embeddings。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        if self.path.endswith("/chat/completions"):
            REC["oa_chat"] += 1
            REC["oa_chat_bodies"].append(body)
            if body.get("response_format") and STATE["oa_rf_fail"] > 0:
                # 只在带 response_format 时拒绝 → 模拟服务端不支持结构化输出
                STATE["oa_rf_fail"] -= 1
                self._json(400, {"error": {"message": "response_format unsupported"}})
                return
            if STATE["oa_chat_fail"] > 0:
                STATE["oa_chat_fail"] -= 1
                self._json(STATE["fail_code"], {"error": {"message": "boom"}})
                return
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for tok in STATE["stream_tokens"]:
                    chunk = {"choices": [{"delta": {"content": tok}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self._json(200, {"choices": [{"message": {"content": STATE["chat_content"]}}]})
        elif self.path.endswith("/embeddings"):
            REC["oa_embed"] += 1
            if STATE["oa_embed_fail"] > 0:
                STATE["oa_embed_fail"] -= 1
                self._json(STATE["fail_code"], {"error": {"message": "boom"}})
                return
            inp = body.get("input") or []
            if isinstance(inp, str):
                inp = [inp]
            self._json(200, {"data": [{"index": i, "embedding": [float(len(t)), 1.0]}
                                      for i, t in enumerate(inp)]})
        else:
            self._json(404, {"error": "no route"})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class StubChatClient:
    """classifier 用：记录每次调用的 format 参数，按队列吐预设回复。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, stream_cb=None, options=None, think=False, format=None):
        self.calls.append({"format": format, "content": messages[0]["content"]})
        return self.replies.pop(0)


def _mk_file(name, content=b"plain text body"):
    p = os.path.join(_SANDBOX, name)
    with open(p, "wb") as f:
        f.write(content)
    return {"path": p, "name": name,
            "ext": os.path.splitext(name)[1].lstrip(".").lower(),
            "size": len(content), "mtime": time.time()}


def test_ollama_format(oc):
    print("\n[1] Ollama format 参数进 payload")
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    oc.chat([{"role": "user", "content": "hi"}])
    check("默认不带 format 字段", "format" not in REC["ol_chat_bodies"][-1],
          str(REC["ol_chat_bodies"][-1]))
    oc.chat([{"role": "user", "content": "hi"}], format=schema)
    check("format 写入 payload", REC["ol_chat_bodies"][-1].get("format") == schema,
          str(REC["ol_chat_bodies"][-1]))
    toks = []
    oc.chat([{"role": "user", "content": "hi"}], stream_cb=toks.append, format=schema)
    check("流式也带 format", REC["ol_chat_bodies"][-1].get("format") == schema
          and toks == ["你", "好"], f"{REC['ol_chat_bodies'][-1]} {toks}")


def test_ollama_format_degrade(oc):
    print("\n[2] Ollama 400 降级链：think → format 逐个去掉重试")
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    # 老服务端（结构化输出需 Ollama 0.5+）：带 format 的请求一律 400。
    # think 与 format 都不被认 → 两次降级后第 3 次请求成功。
    reset_state()
    STATE["ol_fmt_fail"] = 2
    stub, saved = fast_retry()
    try:
        reply = oc.chat([{"role": "user", "content": "hi"}], format=schema)
    finally:
        restore_retry(stub, saved)
    check("两级降级后仍拿到回复", reply == "好的", reply)
    check("请求共 3 次", REC["ol_chat"] == 3, REC["ol_chat"])
    check("第 1 次带 think+format",
          "think" in REC["ol_chat_bodies"][0]
          and REC["ol_chat_bodies"][0].get("format") == schema,
          str(REC["ol_chat_bodies"][0]))
    check("第 2 次去 think 留 format",
          "think" not in REC["ol_chat_bodies"][1]
          and REC["ol_chat_bodies"][1].get("format") == schema,
          str(REC["ol_chat_bodies"][1]))
    check("第 3 次去 format", "format" not in REC["ol_chat_bodies"][2],
          str(REC["ol_chat_bodies"][2]))
    check("降级不触发幂等退避", stub.delays == [], str(stub.delays))

    # 服务端认 think、只偶发拒 format 一次 → 2 次请求（think 重试）即成功
    reset_state()
    STATE["ol_fmt_fail"] = 1
    reply = oc.chat([{"role": "user", "content": "hi"}], format=schema)
    check("仅 format 被拒一次时降级成功", reply == "好的", reply)
    check("仅 format 被拒共 2 次请求", REC["ol_chat"] == 2, REC["ol_chat"])


def test_openai_response_format(oc_api):
    print("\n[3] OpenAI 兼容 response_format 与 400 降级")
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    STATE["oa_rf_fail"] = 1
    stub, saved = fast_retry()
    try:
        reply = oc_api.chat([{"role": "user", "content": "hi"}], format=schema)
    finally:
        restore_retry(stub, saved)
    check("降级后仍拿到回复", reply == "好的", reply)
    check("恰好重试一次（2 次请求）", REC["oa_chat"] == 2, REC["oa_chat"])
    first = REC["oa_chat_bodies"][0]
    check("json_schema 包装形状",
          first.get("response_format") == {"type": "json_schema",
                                           "json_schema": {"name": "result",
                                                           "schema": schema}},
          str(first.get("response_format")))
    check("重试请求已去掉 response_format",
          "response_format" not in REC["oa_chat_bodies"][1],
          str(REC["oa_chat_bodies"][1]))
    check("降级未触发幂等退避", stub.delays == [], str(stub.delays))

    # 流式：format 原样透传、不降级（流式只用于对话 UI）
    reset_state()
    toks = []
    reply = oc_api.chat([{"role": "user", "content": "hi"}],
                        stream_cb=toks.append, format=schema)
    check("流式透传 response_format",
          reply == "你好" and "response_format" in REC["oa_chat_bodies"][-1],
          f"{reply} {REC['oa_chat_bodies'][-1]}")


def test_chat_idempotent_retry(oc, oc_api):
    print("\n[4] 非流式 chat 幂等重试（只对幂等调用重试）")
    # Ollama：500 ×2 后成功 → 共 3 次请求，退避 0.001 → 0.003（×3 指数）
    reset_state()
    STATE["ol_chat_fail"] = 2
    stub, saved = fast_retry()
    try:
        reply = oc.chat([{"role": "user", "content": "hi"}])
    finally:
        restore_retry(stub, saved)
    check("500×2 后成功", reply == "好的", reply)
    check("请求共 3 次", REC["ol_chat"] == 3, REC["ol_chat"])
    check("退避顺序 1×3 递增", stub.delays == [0.001, 0.003], str(stub.delays))

    # OpenAI：429 一次后成功 → 共 2 次
    reset_state()
    STATE["oa_chat_fail"] = 1
    STATE["fail_code"] = 429
    stub, saved = fast_retry()
    try:
        reply = oc_api.chat([{"role": "user", "content": "hi"}])
    finally:
        restore_retry(stub, saved)
    check("429 后重试成功", reply == "好的", reply)
    check("请求共 2 次", REC["oa_chat"] == 2, REC["oa_chat"])
    check("退避只 sleep 一次", stub.delays == [0.001], str(stub.delays))

    # 非流式 4xx（404）不重试
    reset_state()
    STATE["ol_chat_fail"] = 1
    STATE["fail_code"] = 404
    stub, saved = fast_retry()
    try:
        try:
            oc.chat([{"role": "user", "content": "hi"}])
            raised = False
        except Exception:
            raised = True
    finally:
        restore_retry(stub, saved)
    check("404 直接抛错", raised)
    check("404 不重试（1 次请求）", REC["ol_chat"] == 1, REC["ol_chat"])
    check("404 无退避", stub.delays == [], str(stub.delays))


def test_stream_no_retry(oc, oc_api):
    print("\n[5] 流式 chat 不重试")
    reset_state()
    STATE["ol_chat_fail"] = 1
    stub, saved = fast_retry()
    try:
        try:
            oc.chat([{"role": "user", "content": "hi"}],
                    stream_cb=lambda t: None)
            raised = False
        except Exception:
            raised = True
    finally:
        restore_retry(stub, saved)
    check("Ollama 流式失败抛错", raised)
    check("Ollama 流式只请求 1 次", REC["ol_chat"] == 1, REC["ol_chat"])
    check("Ollama 流式无退避", stub.delays == [], str(stub.delays))

    reset_state()
    STATE["oa_chat_fail"] = 1
    stub, saved = fast_retry()
    try:
        try:
            oc_api.chat([{"role": "user", "content": "hi"}],
                        stream_cb=lambda t: None)
            raised = False
        except Exception:
            raised = True
    finally:
        restore_retry(stub, saved)
    check("OpenAI 流式失败抛错", raised)
    check("OpenAI 流式只请求 1 次", REC["oa_chat"] == 1, REC["oa_chat"])
    check("OpenAI 流式无退避", stub.delays == [], str(stub.delays))


def test_embed_retry(oc, oc_api):
    print("\n[6] embed 幂等重试")
    # Ollama：500 ×2 后成功 → 3 次请求，退避 0.001 → 0.003
    reset_state()
    STATE["ol_embed_fail"] = 2
    stub, saved = fast_retry()
    try:
        vecs = oc.embed(["aa", "bbb"])
    finally:
        restore_retry(stub, saved)
    check("embed 500×2 后成功", [v[0] for v in vecs] == [2.0, 3.0], str(vecs))
    check("embed 请求共 3 次", REC["ol_embed"] == 3, REC["ol_embed"])
    check("embed 退避顺序 ×3", stub.delays == [0.001, 0.003], str(stub.delays))

    # OpenAI：429 一次后成功
    reset_state()
    STATE["oa_embed_fail"] = 1
    STATE["fail_code"] = 429
    stub, saved = fast_retry()
    try:
        vecs = oc_api.embed(["aa"])
    finally:
        restore_retry(stub, saved)
    check("embed 429 后成功", vecs and vecs[0][0] == 2.0, str(vecs))
    check("embed 请求共 2 次", REC["oa_embed"] == 2, REC["oa_embed"])

    # OpenAI：400 不重试
    reset_state()
    STATE["oa_embed_fail"] = 1
    STATE["fail_code"] = 400
    stub, saved = fast_retry()
    try:
        try:
            oc_api.embed(["aa"])
            raised = False
        except Exception:
            raised = True
    finally:
        restore_retry(stub, saved)
    check("embed 400 直接抛错", raised)
    check("embed 400 只请求 1 次", REC["oa_embed"] == 1, REC["oa_embed"])
    check("embed 400 无退避", stub.delays == [], str(stub.delays))

    # 连接错误也重试（指向没有服务在听的端口）
    reset_state()
    dead = llm.OpenAICompatClient(base="http://127.0.0.1:9", key="",
                                  chat_model="m", embed_model="e")
    stub, saved = fast_retry()
    try:
        try:
            dead.embed(["aa"])
            raised = False
        except Exception:
            raised = True
    finally:
        restore_retry(stub, saved)
    check("连接错误最终抛错", raised)
    check("连接错误退避 2 次", stub.delays == [0.001, 0.003], str(stub.delays))


def test_classifier():
    print("\n[7] classifier 带 schema 调用 + 解析兜底")
    check("BATCH_FORMAT 枚举 = CATEGORIES",
          classifier.BATCH_FORMAT["items"]["properties"]["category"]["enum"]
          == rules_mod.CATEGORIES)
    check("SINGLE_FORMAT 枚举 = CATEGORIES",
          classifier.SINGLE_FORMAT["properties"]["category"]["enum"]
          == rules_mod.CATEGORIES)

    files = [_mk_file("rb_a.xyz", b"A"), _mk_file("rb_b.xyz", b"BB")]
    # 结构化输出：模型按 schema 返回裸 JSON 数组
    db.set_setting("ai_classify_cache", "{}")
    reply = json.dumps([{"name": "rb_a.xyz", "category": "文档", "reason": "文本"},
                        {"name": "rb_b.xyz", "category": "数据", "reason": "表格"}],
                       ensure_ascii=False)
    stub = StubChatClient([reply])
    r = classifier.ai_classify(files, client=stub)
    check("批量调用带 BATCH_FORMAT", stub.calls[0]["format"] is classifier.BATCH_FORMAT)
    check("结构化输出解析正确",
          r[files[0]["path"]] == ("文档", "文本") and r[files[1]["path"]] == ("数据", "表格"),
          str(r))

    # 兜底：模型无视 schema，把 JSON 数组混在散文里 → 正则+json.loads 仍解析
    db.set_setting("ai_classify_cache", "{}")
    prose = "分类结果如下：" + json.dumps(
        [{"name": "rb_a.xyz", "category": "图片", "reason": "图像"}],
        ensure_ascii=False) + " 以上，请查收。"
    stub = StubChatClient([prose, json.dumps({"category": "字体", "reason": "补漏"},
                                             ensure_ascii=False)])
    r = classifier.ai_classify(files[:1], client=stub)
    check("散文夹 JSON 兜底解析", r[files[0]["path"]] == ("图片", "图像"), str(r))

    # 兜底：非法类别落回「其他」
    db.set_setting("ai_classify_cache", "{}")
    reply = json.dumps([{"name": "rb_a.xyz", "category": "不存在", "reason": "r"}],
                       ensure_ascii=False)
    r = classifier.ai_classify(files[:1], client=StubChatClient([reply]))
    check("非法类别 → 其他", r[files[0]["path"]][0] == "其他", str(r))

    # 批量失败（输出完全不是 JSON）→ 逐个兜底，单个调用带 SINGLE_FORMAT
    db.set_setting("ai_classify_cache", "{}")
    singles = [json.dumps({"category": "视频", "reason": f"r{i}"}, ensure_ascii=False)
               for i in range(len(files))]
    stub = StubChatClient(["抱歉这不是 JSON"] + singles)
    r = classifier.ai_classify(files, client=stub)
    check("批量失败转逐个（3 次调用）", len(stub.calls) == 3, len(stub.calls))
    check("逐个调用带 SINGLE_FORMAT",
          all(c["format"] is classifier.SINGLE_FORMAT for c in stub.calls[1:]),
          str([type(c["format"]).__name__ for c in stub.calls]))
    check("逐个结果正确",
          r[files[0]["path"]][0] == "视频" and r[files[1]["path"]][0] == "视频", str(r))


def test_num_ctx(oc):
    print("\n[8] num_ctx 显式下发（防 Ollama 默认窗口静默截断）")
    reset_state()
    db.set_setting("ollama_num_ctx", "8192")
    try:
        oc.chat([{"role": "user", "content": "hi"}])
        b = REC["ol_chat_bodies"][-1]
        check("文本请求默认注入 num_ctx=8192",
              b.get("options", {}).get("num_ctx") == 8192, str(b.get("options")))

        oc.chat([{"role": "user", "content": "hi"}], options={"num_ctx": 4096})
        check("调用方自带 num_ctx 优先",
              REC["ol_chat_bodies"][-1]["options"]["num_ctx"] == 4096,
              str(REC["ol_chat_bodies"][-1].get("options")))

        db.set_setting("ollama_num_ctx", "16384")
        oc.chat([{"role": "user", "content": "hi"}])
        check("设置项 ollama_num_ctx 覆盖默认",
              REC["ol_chat_bodies"][-1]["options"]["num_ctx"] == 16384,
              str(REC["ol_chat_bodies"][-1].get("options")))

        oc.chat([{"role": "user", "content": "转录图中文字", "images": ["AAAA"]}],
                model="fake-vl")
        check("带图片的请求不下发 num_ctx（视觉调用省显存）",
              "num_ctx" not in REC["ol_chat_bodies"][-1].get("options", {}),
              str(REC["ol_chat_bodies"][-1].get("options")))

        db.set_setting("ollama_num_ctx", "0")
        oc.chat([{"role": "user", "content": "hi"}])
        check("设置 0 = 不干预（跟随模型默认）",
              "num_ctx" not in REC["ol_chat_bodies"][-1].get("options", {}),
              str(REC["ol_chat_bodies"][-1].get("options")))

        db.set_setting("ollama_num_ctx", "not-a-number")
        oc.chat([{"role": "user", "content": "hi"}])
        check("非法设置值回落默认 8192",
              REC["ol_chat_bodies"][-1]["options"]["num_ctx"] == 8192,
              str(REC["ol_chat_bodies"][-1].get("options")))

        # 调用方传入的 options dict 不被就地污染
        caller = {"temperature": 0.3}
        oc.chat([{"role": "user", "content": "hi"}], options=caller)
        check("注入只改请求体，不改调用方的 dict",
              "num_ctx" not in caller and "num_ctx" in REC["ol_chat_bodies"][-1]["options"],
              str(caller))
    finally:
        db.set_setting("ollama_num_ctx", "8192")


def main():
    print(f"APPDATA = {os.environ.get('APPDATA')}")
    if "fb_captest" not in (os.environ.get("APPDATA") or ""):
        print("\n拒绝执行：APPDATA 未指向一次性目录，会污染真实用户库。")
        return 2
    db.init_db()
    db.set_setting("chat_model", "fake-chat")
    db.set_setting("embed_model", "fake-embed")

    ol_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    oa_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    for srv in (ol_srv, oa_srv):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    ol_base = f"http://127.0.0.1:{ol_srv.server_address[1]}"
    oa_base = f"http://127.0.0.1:{oa_srv.server_address[1]}/v1"
    oc = ollama_client.OllamaClient(host=ol_base)
    oc_api = llm.OpenAICompatClient(base=oa_base, key="sk-robust",
                                    chat_model="fake-chat", embed_model="fake-embed")

    try:
        test_ollama_format(oc)
        test_ollama_format_degrade(oc)
        test_openai_response_format(oc_api)
        test_chat_idempotent_retry(oc, oc_api)
        test_stream_no_retry(oc, oc_api)
        test_embed_retry(oc, oc_api)
        test_classifier()
        test_num_ctx(oc)
    finally:
        ol_srv.shutdown()
        oa_srv.shutdown()
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
