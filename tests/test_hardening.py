"""稳定性加固回归测试（沙盒数据目录，不碰真实库）：
连接回收（死线程连接不再泄漏）/ 恢复备份走在线 backup API / watcher 兜底与
钩子防堆积 / 缩略图服务 Host 校验 / LM Studio 本机免密钥接入 / 预览 URL 编码。

运行方式（必须给一次性 APPDATA，目录名需含 fb_，否则拒绝运行防止写真实库）：
  APPDATA=<临时目录> python tests/test_hardening.py
沙盒整体用完即弃，测试内的子目录不做原地清理（数据目录指针已切回默认）。
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db

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


def main():
    appdata = os.environ.get("APPDATA", "")
    if "fb_" not in appdata.lower():
        print("拒绝运行：请设置一次性 APPDATA（路径含 fb_），防止写进真实库。")
        print('  例：APPDATA=C:\\tmp\\fb_hardening python tests/test_hardening.py')
        return 1
    tmp = tempfile.mkdtemp(prefix="fb_hardening_data_", dir=appdata)
    db._apply_data_dir()
    db.close_all_conns()
    db.init_db()
    from backend import fileindex as _fi
    c = db.get_conn()
    c.executescript(_fi.SCHEMA_EXTRA)
    c.commit()
    c.close()

    # ---------- 1. 死线程连接回收 ----------
    import sqlite3 as _s
    db._reap_conns(force=True)
    before = len(db._live_conns)

    def make_and_leak():
        db.get_conn().execute("SELECT 1").fetchone()  # 线程死亡后连接失联
    t = threading.Thread(target=make_and_leak)
    t.start()
    t.join(timeout=5)
    check("死线程连接被登记", len(db._live_conns) > before,
          f"{before} -> {len(db._live_conns)}")
    db._reap_conns(force=True)
    time.sleep(0.1)
    check("reap 后回收死线程连接", len(db._live_conns) == before,
          f"期望 {before}，实际 {len(db._live_conns)}")
    # 活跃线程的缓存连接绝不能被误关
    my_conn = db.get_conn()
    my_conn.execute("SELECT COUNT(*) FROM settings").fetchone()
    db._reap_conns(force=True)
    try:
        my_conn.execute("SELECT 1").fetchone()
        check("活跃连接 reap 后仍可用", True)
    except _s.ProgrammingError:
        check("活跃连接 reap 后仍可用", False, "连接被误关")

    # ---------- 2. 恢复备份：在线 backup API，原子且保留数据 ----------
    db.set_setting("hardening_marker", "v1")
    p1 = db.backup_db()
    check("备份文件生成", bool(p1) and os.path.exists(p1))
    db.set_setting("hardening_marker", "v2")

    r = db.restore_backup(p1)
    check("恢复成功", r.get("ok") is True, str(r))
    check("安全副本存在", os.path.exists(r.get("safety_copy", "")))
    db.close_all_conns()
    val = db.get_setting("hardening_marker")
    check("恢复后数据回到 v1", val == "v1", str(val))
    db.set_setting("hardening_marker", "v2")

    # 恢复失败（损坏文件）：原库不能被破坏
    fd, bad = tempfile.mkstemp(suffix=".db", dir=tmp)
    os.write(fd, b"this is not a sqlite database at all")
    os.close(fd)
    r2 = db.restore_backup(bad)
    check("损坏备份恢复失败并报错", r2.get("ok") is False, str(r2))
    db.close_all_conns()
    check("失败后原库完好", db.get_setting("hardening_marker") == "v2",
          str(db.get_setting("hardening_marker")))

    # ---------- 3. 切换数据目录：backup API 复制，数据完整 ----------
    target = os.path.join(appdata, "fb_hardening_switch_target")
    db.set_setting("hardening_marker", "moved")
    r3 = db.switch_data_dir(target, overwrite=True, mode="copy")
    check("切换数据目录成功", r3.get("ok") is True, str(r3))
    new_db = os.path.join(target, "filebutler.db")
    chk = _s.connect(new_db)
    row = chk.execute("SELECT value FROM settings WHERE key='hardening_marker'").fetchone()
    chk.close()
    check("新库带全部数据", row is not None and row[0] == "moved", str(row))
    # 清指针切回默认，继续测试
    db.set_data_dir(None)
    db._apply_data_dir()
    db.close_all_conns()

    # ---------- 4. watcher：worker 异常兜底 + 钩子防堆积 ----------
    from backend import watcher as _w
    eng = _w.WatchEngine()

    def boom():
        raise RuntimeError("boom")
    eng._drain_queue = boom
    eng._stop.clear()
    wt = threading.Thread(target=eng._run, daemon=True)
    wt.start()
    time.sleep(0.4)
    alive = wt.is_alive()
    eng._stop.set()
    wt.join(timeout=3)
    check("worker 循环异常不致死", alive, "worker 线程已退出")
    check("worker 正常停止", not wt.is_alive())
    log_path = os.path.join(db.get_data_dir(), "watcher.log")
    check("异常留痕 watcher.log", os.path.exists(log_path))

    released = threading.Event()
    calls = []

    def slow_hook(paths):
        calls.append(paths)
        released.wait(timeout=5)

    def flush(eng):
        eng._last_flush = 0.0  # 绕过 30s/20个 的攒批门槛，直接触发
        eng._maybe_flush_hooks()

    eng2 = _w.WatchEngine(on_docs=slow_hook)
    eng2._doc_queue = ["a.txt", "b.txt"]
    flush(eng2)
    time.sleep(0.2)
    check("钩子线程已起", eng2._hook_busy.get("docs") is True)
    eng2._doc_queue = ["c.txt", "d.txt"]
    flush(eng2)
    check("在飞时不重复起线程", len(eng2._doc_queue) == 2, str(eng2._doc_queue))
    time.sleep(0.1)
    check("队列不因防堆积丢文件", len(eng2._doc_queue) == 2, str(eng2._doc_queue))
    released.set()
    deadline = time.time() + 5
    while time.time() < deadline and eng2._hook_busy.get("docs"):
        time.sleep(0.05)
    check("钩子结束后释放", not eng2._hook_busy.get("docs"))
    eng2._doc_queue = ["e.txt"]
    flush(eng2)
    time.sleep(0.2)
    check("下一批重新起线程", len(calls) == 2 and calls[-1] == ["e.txt"], str(calls))

    # ---------- 5. 缩略图服务 Host 校验 ----------
    import requests as _rq
    from backend import thumbserver as _ts
    from PIL import Image as _Img
    root = os.path.join(tmp, "watched_root")
    os.makedirs(root, exist_ok=True)
    _fi.add_root(root)
    img = os.path.join(root, "pic.png")
    _Img.new("RGB", (8, 8), (200, 30, 30)).save(img, "PNG")
    tricky = os.path.join(root, "a&b#c 附 件.png")
    _Img.new("RGB", (8, 8), (30, 200, 30)).save(tricky, "PNG")

    srv = _ts.ThumbServer()
    port = srv.start()
    from urllib.parse import quote
    url = f"http://127.0.0.1:{port}/thumb?p={quote(img, safe='')}"
    try:
        resp = _rq.get(url, headers={"Host": "evil.example.com"}, timeout=10)
        check("非回环 Host 被拒", resp.status_code == 403, str(resp.status_code))
        resp2 = _rq.get(url, timeout=10)  # requests 按 URL 设 Host=127.0.0.1:port
        check("回环 Host 放行", resp2.status_code == 200, str(resp2.status_code))
        check("缩略图确实生成", resp2.status_code == 200 and
              resp2.headers.get("Content-Type") == "image/jpeg")
    finally:
        srv.stop()

    # ---------- 6. LM Studio：本机免密钥 + 预设 + 校验 ----------
    from backend import llm as _llm
    check("预设含 LM Studio", any(p["label"].startswith("LM Studio")
                                  for p in _llm.API_PRESETS))
    check("is_local_base 回环判定", _llm.is_local_base("http://127.0.0.1:1234/v1")
          and _llm.is_local_base("http://LocalHost:1234/v1")
          and not _llm.is_local_base("https://api.deepseek.com/v1"))

    from backend.api import Api
    api = Api()
    ok = api.save_llm_provider("api", "http://127.0.0.1:1234/v1", "", "qwen3-8b")
    check("LM Studio 免密钥可保存", ok.get("ok") is True, str(ok))
    bad_cloud = api.save_llm_provider("api", "https://api.deepseek.com/v1", "", "m")
    check("云端无密钥被拒", bad_cloud.get("ok") is False
          and "密钥" in bad_cloud.get("error", ""), str(bad_cloud))
    saved = api.save_llm_provider("api", "https://api.deepseek.com/v1",
                                  "sk-test-abcd1234wxyz", "deepseek-chat")
    check("云端带密钥可保存", saved.get("ok") is True, str(saved))
    masked = _llm.api_key_masked()
    check("打码回显只露尾 4 位", masked == "sk-****wxyz", masked)
    keep = api.save_llm_provider("api", "https://api.deepseek.com/v1",
                                 "", "deepseek-reasoner")
    check("已存密钥留空保存沿用不报错", keep.get("ok") is True
          and _llm.api_key() == "sk-test-abcd1234wxyz", str(keep))
    check("打码值随 provider_summary 下发",
          keep.get("summary", {}).get("api_key_masked") == "sk-****wxyz")
    check("点眼睛取回明文密钥",
          api.reveal_api_key().get("key") == "sk-test-abcd1234wxyz")
    bad_scheme = api.save_llm_provider(
        "api", "ftp" + "://127.0.0.1:1234/v1", "k", "m")
    check("非 http(s) 地址被拒", bad_scheme.get("ok") is False
          and "http" in bad_scheme.get("error", ""), str(bad_scheme))
    check("云端向量模型缺失被拒", api.save_llm_provider(
        "api", "http://127.0.0.1:1234/v1", "", "m", "api", "").get("ok") is False)
    check("list_remote_models 拒绝非 http",
          _llm.list_remote_models("ftp" + "://x")["ok"] is False)

    # ---------- 7. 预览 URL 编码 + keep_alive 设置 ----------
    pv = api.preview_file(tricky)
    u = pv.get("url", "")
    check("预览 URL 已编码", "a%26b%23" in u and u.count("?") == 1, u)
    check("keep_alive 默认 30m",
          db.get_setting("ollama_keep_alive", "30m") == "30m")
    db.set_setting("ollama_keep_alive", "-1")
    from backend.ollama_client import OllamaClient
    check("keep_alive 设置生效", OllamaClient()._keep_alive() == "-1")

    # ---------- 8. HTTP 错误透传服务端详情 ----------
    from backend.ollama_client import http_error_detail
    resp = _rq.Response()
    resp.status_code = 400
    resp._content = b'{"error": {"message": "temperature must be greater than 0"}}'
    resp.headers["Content-Type"] = "application/json"
    err = _rq.HTTPError("400 Client Error", response=resp)
    check("JSON 错误体被解析出 message", http_error_detail(err) ==
          "HTTP 400: temperature must be greater than 0", http_error_detail(err))
    resp2 = _rq.Response()
    resp2.status_code = 403
    resp2._content = b"invalid api key format"
    err2 = _rq.HTTPError("403", response=resp2)
    check("纯文本错误体被带出", "invalid api key format" in http_error_detail(err2),
          http_error_detail(err2))
    check("无响应体的错误不崩", http_error_detail(
        _rq.HTTPError("boom")).startswith("boom"),
        http_error_detail(_rq.HTTPError("boom")))
    check("预设含小米 MiMo", any(p["label"] == "小米 MiMo" for p in _llm.API_PRESETS))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
