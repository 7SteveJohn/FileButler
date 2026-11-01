"""统一测试入口：一条命令顺序跑全部 test_*.py 套件（贡献者 / CI 用）。

用法：
  python tests/run_all.py                 # 默认：跳过需要真实服务的 E2E 套件
  FB_RUN_E2E=1 python tests/run_all.py    # 连需要真实 Ollama 的 E2E 套件一起跑

注意：用装好 requirements.txt 的解释器运行（CI 的 setup-python 已装；
本机开发用 .venv/Scripts/python.exe），否则缺依赖的套件会按 FAIL 记。

约定（与 .github/workflows/ci.yml 对齐）：
  * 子进程环境带 PYTHONUTF8=1 / PYTHONIOENCODING=utf-8（CI runner 的控制台
    代码页不是 UTF-8，套件里大量中文 print 必须钉死编码）与 FB_SKIP_WATCHER=1
    （runner 上 watcher 断言无意义，消费方在 tests/test_fileindex.py）。
  * 每个套件给一个一次性 APPDATA（路径含 fb_，test_hardening /
    test_ollama_models_dir 有目录名门禁，否则拒绝运行），防止任何套件写进
    真实用户库；套件自己 tempfile.mkdtemp 自隔离 APPDATA 的会覆盖它，互不影响。
  * 失败判定 = 退出码非 0，或输出匹配 ^\\s*FAIL / FAILURES: / Traceback。
    不能只看退出码：test_hybrid / test_rag / test_parse_chunk 的入口是裸
    main() 不传退出码（test_ai_classify 甚至没有 __main__ 守卫），口径与
    ci.yml 的输出判定一致。
  * 顺序执行不并发：各套件虽然自带 APPDATA 沙箱，但不引入并发变量。
  * tests/spikes/ 与 eval_retrieval.py 不是 test_*.py 套件，不会被发现。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS_DIR)
_TIMEOUT = 600  # 单套件秒数上限；卡死的套件按 FAIL 处理，别让 CI 整体挂死

# 个别套件对 APPDATA 目录名有更严的子串门禁（不是认 fb_ 就行），给专用目录名。
# 对应表与 ci.yml 的 $appdataNames 一致：
APPDATA_NAMES = {
    # test_datadir.py:30 要求 APPDATA 含 "fb_datadir"
    "test_datadir": "fb_datadir",
    # test_session_followups.py:243 要求 APPDATA 含 "fb_captest"
    "test_session_followups": "fb_captest",
}

# 这些套件自己不调 db.init_db() 也不隔离 APPDATA（直接跑靠外部已有库），
# 在全新沙盒 APPDATA 里会因没有 settings 表而崩。先在沙盒里初始化一个空库再跑。
PRE_INIT_DB = {"test_status_codes"}

# 需要真实服务（真实 Ollama / 真实模型）的套件，默认跳过；FB_RUN_E2E=1 时才跑。
# 清单是逐个看各套件文件头注释识别出来的：
SKIP_E2E = {
    # 文件头：「知识库端到端测试：…（需 Ollama + bge-m3）」
    "test_rag": "需真实 Ollama + bge-m3（文件头：知识库端到端，解析→索引→语义搜索→RAG 问答）",
    # 文件头：「AI 分类实测（需 Ollama 运行）」
    "test_ai_classify": "需真实 Ollama 运行（文件头：AI 分类实测）",
    # Ollama 未运行时该套件自跳过 AI 部分（test_upgrade.py:47-49），但 Ollama 在跑时
    # 会做真实批量 embed/分类，结果随本机模型配置漂移（其头注释自述实测教训），CI 也不跑它
    "test_upgrade": "Ollama 在跑时做真实批量 embed/分类，结果随本机模型配置漂移；CI 也不跑",
}

# 失败输出的特征（同 ci.yml 的判定口径）
_FAIL_RE = re.compile(r"(?m)^\s*FAIL|FAILURES:|Traceback \(most recent call last\)")
_FAIL_TAIL_LINES = 15  # 失败时摘录输出的最后几行


def _discover():
    """发现 tests/ 下与 run_all.py 同目录的 test_*.py（不含子目录）。"""
    return sorted(
        f[:-3] for f in os.listdir(_TESTS_DIR)
        if f.startswith("test_") and f.endswith(".py")
    )


def _child_env(appdata):
    """子进程环境：编码钉死 UTF-8、关 watcher、APPDATA 指向一次性沙盒。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["FB_SKIP_WATCHER"] = "1"
    env["APPDATA"] = appdata
    return env


def _run_one(name, appdata):
    """顺序跑一个套件，返回 (status, elapsed, note, tail)。"""
    env = _child_env(appdata)
    if name in PRE_INIT_DB:
        # 沙盒里先建空库（cwd=仓库根，-c 模式下 cwd 自动进 sys.path）
        subprocess.run(
            [sys.executable, "-c", "from backend import db; db.init_db()"],
            cwd=_ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=60,
        )
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(_TESTS_DIR, name + ".py")],
            cwd=_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.perf_counter() - t0
        out = (e.stdout or b"").decode("utf-8", "replace")
        tail = "\n".join(out.splitlines()[-_FAIL_TAIL_LINES:])
        return "FAIL", elapsed, f"超时（>{_TIMEOUT}s 强制终止）", tail
    elapsed = time.perf_counter() - t0
    out = proc.stdout.decode("utf-8", "replace")
    lines = out.splitlines()
    if proc.returncode != 0 or _FAIL_RE.search(out):
        tail = "\n".join(lines[-_FAIL_TAIL_LINES:])
        note = f"退出码 {proc.returncode}" if proc.returncode != 0 else "输出含 FAIL 标记"
        return "FAIL", elapsed, note, tail
    return "PASS", elapsed, "", ""


def main():
    # run_all 自己要打印中文：GBK 控制台下个别字符会炸 UnicodeEncodeError，
    # 改成用替代符输出，不改变编码本身
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    run_e2e = os.environ.get("FB_RUN_E2E") == "1"
    suites = _discover()
    tmp_root = tempfile.mkdtemp(prefix="fb_runall_")
    rows = []       # (name, status, elapsed, note)
    failures = []   # (name, tail)
    try:
        for name in suites:
            if not run_e2e and name in SKIP_E2E:
                rows.append((name, "SKIP", 0.0, SKIP_E2E[name]))
                print(f"  {name}  SKIP（需要真实服务）", flush=True)
                continue
            appdata = os.path.join(tmp_root, APPDATA_NAMES.get(name, f"fb_runall_{name}"))
            os.makedirs(appdata, exist_ok=True)
            status, elapsed, note, tail = _run_one(name, appdata)
            rows.append((name, status, elapsed, note))
            if status == "FAIL":
                failures.append((name, tail))
            print(f"  {name}  {status}（{elapsed:.1f}s）", flush=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ---------- 汇总表 ----------
    print("\n========== 测试汇总 ==========")
    print(f"{'套件':<22}  {'结果':<6}{'耗时(s)':<10}说明")
    n_pass = n_fail = n_skip = 0
    for name, status, elapsed, note in rows:
        cost = f"{elapsed:.1f}" if status != "SKIP" else "-"
        print(f"{name:<22}  {status:<6}{cost:<10}{note or ''}")
        if status == "PASS":
            n_pass += 1
        elif status == "FAIL":
            n_fail += 1
        else:
            n_skip += 1
    summary = f"共 {len(rows)} 个套件：通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}"
    if not run_e2e:
        summary += "（跳过的为需要真实服务的 E2E 套件，FB_RUN_E2E=1 可启用）"
    print(f"\n{summary}")

    for name, tail in failures:
        print(f"\n----- 失败摘录：{name}（最后 {_FAIL_TAIL_LINES} 行）-----")
        print(tail)

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
