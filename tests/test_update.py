"""自动更新测试：版本比较、安装包下载（进度/校验/失败清理）、安装入口参数校验。

不起真实 GUI，也不访问外网：本地起一个 http.server 提供假 release JSON 与假安装包。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_update_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                      # noqa: E402
from backend.api import Api                 # noqa: E402

PASS, FAIL = 0, 0
PKG = b"MZ" + b"fake-installer-payload" * 400      # 约 8.6 KB


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


class Handler(BaseHTTPRequestHandler):
    mode = {"digest": "sha256:" + hashlib.sha256(PKG).hexdigest()}

    def log_message(self, *a):
        pass

    def do_GET(self):
        port = self.server.server_port
        if self.path == "/latest":
            body = json.dumps({
                "tag_name": "v9.9.9",
                "assets": [{
                    "name": "FileButler-Setup-9.9.9.exe",
                    "browser_download_url": "http://127.0.0.1:%d/pkg.exe" % port,
                    "digest": self.mode["digest"],
                }],
                "body": "test release",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/pkg.exe":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(PKG)))
            self.end_headers()
            self.wfile.write(PKG)
        else:
            self.send_response(404)
            self.end_headers()


class FakeWindow:
    """把 _emit 推的 JS 还原成事件列表。"""

    def __init__(self):
        self.events = []

    def evaluate_js(self, js):
        try:
            data = json.loads(js[js.index("(") + 1: js.rindex(")")])
            self.events.append(data)
        except Exception:
            pass


def wait_event(win, name, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for e in win.events:
            if e.get("name") == name:
                return e.get("payload")
        time.sleep(0.05)
    return None


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_port

    db.init_db()
    db.set_setting("update_check_url", base + "/latest")
    api = Api()
    win = FakeWindow()
    api._window = win
    api.UPDATE_DIR = tempfile.mkdtemp(prefix="fb_upd_")   # 不写到真实 %TEMP%

    # 1. 版本比较
    info = api.check_update()
    check("check_update ok", info.get("ok") is True, str(info)[:120])
    check("outdated detected", info.get("outdated") is True, str(info)[:120])
    check("digest extracted", str(info.get("digest", "")).startswith("sha256:"), str(info.get("digest")))

    # 2. 下载成功路径
    r = api.download_update()
    check("download started", r.get("ok") is True, str(r))
    payload = wait_event(win, "update_done")
    check("update_done fired", payload is not None, str(win.events[-1:]))
    dest = (payload or {}).get("path", "")
    check("file written", bool(dest) and os.path.isfile(dest), dest)
    if os.path.isfile(dest):
        got = hashlib.sha256(open(dest, "rb").read()).hexdigest()
        check("sha256 matches", got == hashlib.sha256(PKG).hexdigest())
    check("progress emitted", any(e["name"] == "update_progress" for e in win.events))

    # 3. 校验失败 → 丢弃半成品（先清掉上一步成功下载的包，只看本次残留）
    win.events.clear()
    for f in os.listdir(api.UPDATE_DIR):
        os.remove(os.path.join(api.UPDATE_DIR, f))
    Handler.mode["digest"] = "sha256:" + "0" * 64
    r = api.download_update()
    check("download restarted", r.get("ok") is True, str(r))
    err = wait_event(win, "update_error")
    check("bad digest rejected", err is not None and "校验" in str(err), str(err))
    left = [f for f in os.listdir(api.UPDATE_DIR) if f.endswith((".part", ".exe"))]
    check("no leftover file", left == [], str(left))

    # 4. 安装入口：路径必须在更新目录下
    outside = os.path.join(tempfile.gettempdir(), "FileButler-Setup-9.9.9.exe")
    r = api.install_update(outside)
    check("reject path outside update dir", r.get("ok") is False, str(r))
    Handler.mode["digest"] = "sha256:" + hashlib.sha256(PKG).hexdigest()
    win.events.clear()
    api.download_update()
    good = wait_event(win, "update_done")
    inside = (good or {}).get("path", "")
    r = api.install_update(inside)
    check("reject in dev mode (not frozen)", r.get("ok") is False, str(r))
    r = api.install_update(os.path.join(api.UPDATE_DIR, "not-an-installer.exe"))
    check("reject wrong filename", r.get("ok") is False, str(r))

    # 5. 回滚：从 release 列表里找「比当前版本小的最近一个」
    from backend import __version__ as cur_ver
    fake = [
        {"tag_name": "v99.0.0", "assets": [{"name": "FileButler-Setup-99.0.0.exe",
                                            "browser_download_url": base + "/pkg.exe"}]},
        {"tag_name": "v1.2.1", "assets": [{"name": "FileButler-Setup-1.2.1.exe",
                                           "browser_download_url": base + "/pkg.exe",
                                           "digest": "sha256:" + hashlib.sha256(PKG).hexdigest()}]},
        {"tag_name": "v1.1.6", "assets": [{"name": "FileButler-Setup-1.1.6.exe",
                                           "browser_download_url": base + "/pkg.exe"}]},
    ]
    api._list_releases = lambda limit=30: fake
    t = api.rollback_target()
    check("rollback target is previous", t.get("ok") is True and t.get("target") == "v1.2.1", str(t))
    check("rollback ignores newer release", t.get("target") != "v99.0.0", str(t))

    win.events.clear()
    r = api.download_rollback()
    check("rollback download started", r.get("ok") is True, str(r))
    done = wait_event(win, "update_done")
    check("rollback download done", done is not None and done.get("rollback") is True, str(done))

    api._list_releases = lambda limit=30: [fake[0]]
    t = api.rollback_target()
    check("no older release -> error", t.get("ok") is False and "更早" in str(t.get("error")), str(t))
    check("_ver_key pads", Api._ver_key("v1.4") == [1, 4, 0], str(Api._ver_key("v1.4")))
    check("_ver_key rejects junk", Api._ver_key("nightly") is None)
    check("current version parsed", Api._ver_key(cur_ver) is not None, cur_ver)

    srv.shutdown()
    shutil.rmtree(api.UPDATE_DIR, ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
