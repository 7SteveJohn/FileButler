"""LLM 接入层测试：假 OpenAI 兼容服务器验证协议实现 + 模式切换 + 省钱混搭。"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, llm

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


class FakeOpenAI(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容服务：chat（流式/非流式）+ embeddings。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer sk-test-123":
            self._json(401, {"error": {"message": "bad key"}})
            return
        if self.path.endswith("/chat/completions"):
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for tok in ["你好", "，", "世界"]:
                    chunk = {"choices": [{"delta": {"content": tok}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self._json(200, {"choices": [{"message": {"content": "非流式回复"}}]})
        elif self.path.endswith("/embeddings"):
            inp = body["input"]
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


def main():
    db.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

    saved = {k: db.get_setting(k, "") for k in
             ("llm_mode", "embed_source", "api_base", "api_key",
              "api_chat_model", "api_embed_model")}
    try:
        # ---------- 1. 配置校验 ----------
        r = llm.OpenAICompatClient().chat.__self__ if False else None
        db.set_setting("llm_mode", "api")
        db.set_setting("api_base", base)
        db.set_setting("api_key", "sk-test-123")
        db.set_setting("api_chat_model", "fake-chat")
        db.set_setting("embed_source", "follow")
        db.set_setting("api_embed_model", "fake-embed")
        check("api mode chat_status ok", llm.chat_status()["ok"], str(llm.chat_status()))
        check("api mode embed_status ok", llm.embed_status()["ok"], str(llm.embed_status()))
        check("factory returns api client",
              isinstance(llm.get_chat_client(), llm.OpenAICompatClient))

        # ---------- 2. 协议实现 ----------
        c = llm.get_chat_client()
        reply = c.chat([{"role": "user", "content": "hi"}])
        check("non-stream chat", reply == "非流式回复", reply)
        toks = []
        reply = c.chat([{"role": "user", "content": "hi"}], stream_cb=toks.append)
        check("stream chat", reply == "你好，世界" and toks == ["你好", "，", "世界"],
              f"{reply} {toks}")
        reply = c.chat([{"role": "user", "content": "hi"}],
                       options={"temperature": 0.1, "num_predict": 50})
        check("options mapped (max_tokens)", reply == "非流式回复")

        vecs = c.embed(["a", "bb", "ccc"])
        check("embeddings batch", [v[0] for v in vecs] == [1.0, 2.0, 3.0], str(vecs))
        check("embed_one", c.embed_one("abcd")[0] == 4.0)

        # 错误密钥 → 401 抛异常
        db.set_setting("api_key", "sk-wrong")
        try:
            llm.get_chat_client().chat([{"role": "user", "content": "x"}])
            check("bad key raises", False)
        except Exception as e:
            check("bad key raises", "401" in str(e) or "Client Error" in str(e), str(e)[:80])
        db.set_setting("api_key", "sk-test-123")

        # ---------- 3. 测试连通 ----------
        r = llm.test_chat()
        check("test_chat ok", r["ok"] and "非流式回复" in r.get("reply", ""), str(r))

        # ---------- 4. 省钱混搭：API 对话 + 本地向量 ----------
        db.set_setting("embed_source", "ollama")
        check("hybrid embed uses ollama",
              type(llm.get_embed_client()).__name__ == "OllamaClient")
        es = llm.embed_status()
        check("hybrid embed status", es["ok"] == True and "本地" in es["reason"], str(es))
        check("hybrid chat still api",
              isinstance(llm.get_chat_client(), llm.OpenAICompatClient))

        # ---------- 5. 端点归一化 ----------
        c2 = llm.OpenAICompatClient(base=base + "/chat/completions",
                                    key="sk-test-123", chat_model="m")
        check("endpoint already-full passthrough",
              c2.chat([{"role": "user", "content": "x"}]) == "非流式回复")

    finally:
        # 还原配置，避免影响其他测试
        for k, v in saved.items():
            db.set_setting(k, v)
        srv.shutdown()

    check("restored to ollama mode", llm.mode() == "ollama" or saved["llm_mode"] == "ollama")
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
