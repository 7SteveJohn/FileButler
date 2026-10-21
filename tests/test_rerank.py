"""rerank 精排测试：rag 集成（重排/回退/池扩容）+ reranker 单元 + 真模型冒烟（有模型才跑）。

打桩：rag._retrieve / rag._rows_in_order / reranker.rerank_scores，不依赖 Ollama 与 ONNX。
最后一段「真模型冒烟」在本机已下载模型且依赖齐全时用真实 ONNX 跑一次打分，
否则打印 SKIP——套件在任何环境都自足。

运行：.venv/Scripts/python tests/test_rerank.py
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_rerank_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, llm  # noqa: E402
from backend.knowledge import rag, reranker  # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


_ROWS = {
    1: {"file_path": "甲.md", "text": "甲.md · 运费\n运费结算周期 T+7。"},
    2: {"file_path": "乙.md", "text": "乙.md · 菜单\n本周食堂菜单：红烧肉。"},
    3: {"file_path": "丙.md", "text": "丙.md · 报销\n报销单需附发票原件。"},
}
_PICKED_RRF = [(1, 0.9), (2, 0.8), (3, 0.7)]  # RRF 给出的名次：甲 > 乙 > 丙


class _State:
    retrieve_k = None
    rerank_calls = []


def _stub_retrieve(query, k):
    _State.retrieve_k = k
    return list(_PICKED_RRF), "hybrid", None


def _stub_rows(picked):
    return dict(_ROWS)


class _StubReranker:
    """按脚本给每块打分：丙最高、甲其次、乙最低——与 RRF 名次相反。"""

    def __init__(self, scores=None, boom=False):
        self.scores = scores or {1: 0.4, 2: 0.1, 3: 0.9}
        self.boom = boom

    def __call__(self, query, texts):
        _State.rerank_calls.append((query, list(texts)))
        if self.boom:
            return None
        # 传入 texts 顺序 = picked 的 ids 顺序（甲乙丙）
        return [self.scores[i] for i in (1, 2, 3)]


class _FakeChat:
    def chat(self, messages, stream_cb=None, options=None, think=False):
        if stream_cb:
            stream_cb("依据资料作答。 来源 [1]")
            return "依据资料作答。 来源 [1]"
        return "改写词"


def _patch(retrieve=_stub_retrieve, rows=_stub_rows, rerank_obj=None, active=True):
    """打桩三件套 + llm 对话可用；返回还原函数。"""
    orig = (rag._retrieve, rag._rows_in_order, llm.chat_available,
            llm.get_chat_client, llm.embed_available,
            reranker.active, reranker.rerank_scores)
    rag._retrieve = retrieve
    rag._rows_in_order = rows
    llm.chat_available = lambda: True
    llm.get_chat_client = lambda: _FakeChat()
    llm.embed_available = lambda: False
    reranker.active = lambda: active
    if rerank_obj is not None:
        reranker.rerank_scores = rerank_obj
    def _restore():
        (rag._retrieve, rag._rows_in_order, llm.chat_available,
         llm.get_chat_client, llm.embed_available,
         reranker.active, reranker.rerank_scores) = orig
    return _restore


def test_rag_rerank_order():
    print("\n[1] ask/search 走 rerank 重排")
    _State.rerank_calls.clear()
    restore = _patch(rerank_obj=_StubReranker(), active=True)
    try:
        r = rag.ask("报销要带什么")
        order = [s["file"] for s in r["sources"]]
        check("ask 来源按 rerank 分重排（丙>甲>乙）", order == ["丙.md", "甲.md", "乙.md"], str(order))
        check("ask 检索池扩到 RERANK_POOL", _State.retrieve_k == rag.RERANK_POOL,
              str(_State.retrieve_k))
        check("rerank 收到 query 与三块原文",
              _State.rerank_calls and _State.rerank_calls[0][0] == "报销要带什么"
              and len(_State.rerank_calls[0][1]) == 3)
        check("rerank 分数进入 sources.score", r["sources"][0]["score"] == 0.9, str(r["sources"][0]))

        _State.rerank_calls.clear()
        s = rag.search("报销")
        check("search 结果同序重排",
              [x["file_path"] for x in s["results"]] == ["丙.md", "甲.md", "乙.md"],
              str([x["file_path"] for x in s["results"]]))
        check("search 附 reranked 标记", s.get("reranked") is True, str(s.get("reranked")))
    finally:
        restore()


def test_rerank_fallback():
    print("\n[2] rerank 失败/关闭 → 回退 RRF 原序")
    # 打分返回 None（加载失败/异常路径）
    restore = _patch(rerank_obj=_StubReranker(boom=True), active=True)
    try:
        r = rag.ask("报销要带什么")
        order = [s["file"] for s in r["sources"]]
        check("打分失败回退 RRF 原序", order == ["甲.md", "乙.md", "丙.md"], str(order))
    finally:
        restore()

    # rerank 未启用：池不扩容、不调打分
    _State.rerank_calls.clear()
    restore = _patch(rerank_obj=_StubReranker(), active=False)
    try:
        r = rag.ask("报销要带什么", k=8)
        check("关闭时检索池 = k（不扩容）", _State.retrieve_k == 8, str(_State.retrieve_k))
        check("关闭时不发起打分调用", not _State.rerank_calls)
        check("关闭时仍按 RRF 原序回答", [s["file"] for s in r["sources"]]
              == ["甲.md", "乙.md", "丙.md"])
    finally:
        restore()

    # 只有 1 条候选：不打分直接返回
    def _one(query, k):
        return [(1, 0.9)], "vector", None
    _State.rerank_calls.clear()
    restore = _patch(retrieve=_one, rerank_obj=_StubReranker(), active=True)
    try:
        rag.ask("随便问")
        check("单候选跳过打分", not _State.rerank_calls)
    finally:
        restore()


def test_reranker_unit():
    print("\n[3] reranker 单元（开关/状态/降级）")
    reranker.set_enabled(True)
    check("开关写入", reranker.enabled() is True)
    reranker.set_enabled(False)
    check("开关关闭", reranker.enabled() is False)

    st = reranker.status()
    for key in ("enabled", "runtime", "downloaded", "active", "model_dir", "download"):
        check(f"status 含 {key}", key in st, str(st)[:120])
    check("未启用时 active=False", reranker.active() is False)
    check("未启用时 rerank_scores=None",
          reranker.rerank_scores("q", ["a", "b"]) is None)
    check("模型未下载时 model_ready=False", reranker.model_ready() is False)
    # 模型「已就绪」时 start_download 不重复下载
    orig = reranker.model_ready
    reranker.model_ready = lambda: True
    try:
        r = reranker.start_download()
        check("已就绪时 start_download 直接返回 ready", r.get("ready") is True, str(r))
    finally:
        reranker.model_ready = orig
    check("delete_model 空目录也安全", reranker.delete_model().get("ok") is True)


def test_real_model_smoke():
    print("\n[4] 真模型冒烟（已下载才跑）")
    reranker.set_enabled(True)
    try:
        if not (reranker.runtime_available() and reranker.model_ready()):
            print("  SKIP 运行时或模型缺失（本套件在无模型环境自足）")
            return
        scores = reranker.rerank_scores(
            "报销单的审批流程是什么",
            ["差旅费用报销需先在OA系统提交报销单，主管审批后财务打款。",
             "公司食堂本周菜单：周一红烧肉，周三清蒸鱼。"])
        check("真模型返回两个分数", scores is not None and len(scores) == 2, str(scores))
        if scores:
            check("真模型相关性排序正确（报销流程 > 食堂菜单）",
                  scores[0] > scores[1], str(scores))
    finally:
        reranker.set_enabled(False)


def main():
    print(f"APPDATA = {os.environ.get('APPDATA')}")
    if "fb_captest" not in (os.environ.get("APPDATA") or ""):
        print("\n拒绝执行：APPDATA 未指向一次性目录。")
        return 2
    db.init_db()
    try:
        test_rag_rerank_order()
        test_rerank_fallback()
        test_reranker_unit()
        test_real_model_smoke()
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
          f"({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
