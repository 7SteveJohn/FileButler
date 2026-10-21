"""知识库问答升级测试：多轮检索改写 + 引用增强（title/locator/cited）。全部打桩，不需要 Ollama。

覆盖：
  1. _split_prefix / _locator / _parse_citations 纯函数
  2. history 非空时检索用改写后的 query（改写词命中、原词必落空的用例）
  3. 改写异常 / 输出为空 / 超长 / 裹引号多行 → 回退原 query 或取首行清洗
  4. 无 history 不发起改写调用
  5. sources 的 title / locator 解析与 snippet 去前缀
  6. cited 引用编号校验（越界过滤、去重、无引用为空）
  7. 清单意图不触发改写（原行为不变）

运行：.venv/Scripts/python tests/test_kb_followup.py
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_kbfollowup_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, llm  # noqa: E402
from backend.knowledge import rag  # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


# 块文本带 chunker 前缀行「文件名 · 所在标题」（indexer 落库的真实形态）。
# 追问原词「经手人是谁」与全部块无共享二元组——不改写必落空；
# 改写文案含「周敏」，只有签署信息块命中。
_DOC_A = "蓝湖物流合同.md"
REWRITE_TEXT = "蓝湖物流合同的经办人周敏是谁"
_CHUNK_FEE = _DOC_A + " · 运费结算\n合同编号 LH-2025-077，运费结算周期 T+7，逾期按日千分之三计违约金。"
_CHUNK_SIGN = _DOC_A + " · 签署信息\n本合同经办人为业务经理周敏，签署地点杭州。"
_CHUNK_MD = "报销制度.md · 差旅报销\n一线城市住宿每晚 500 元。"
_CHUNK_PPT = "季度汇报.pptx · [第3页]\n第三季度营收要点：毛利率环比提升。"
_CHUNK_XLSX = "报价单.xlsx\n[工作表 报价]\n产品 | 金额\nA 型号 | 199"

_HISTORY = [{"role": "user", "content": "蓝湖物流合同的运费怎么结算"},
            {"role": "assistant", "content": "按 T+7 周期结算，逾期有违约金。"}]


class FakeChatClient:
    """按调用形态分流：非流式调用视为检索改写（返回固定改写文案），
    流式调用视为最终生成（回调并返回固定答案）。两类调用都留痕供断言。"""

    def __init__(self, rewrite="", answer="依据资料作答。"):
        self.rewrite = rewrite
        self.answer = answer
        self.rewrite_calls = []    # [{"messages", "options", "think"}]
        self.answer_calls = 0
        self.answer_messages = []

    def chat(self, messages, stream_cb=None, options=None, think=False):
        if stream_cb:
            self.answer_calls += 1
            self.answer_messages.append(messages)
            stream_cb(self.answer)
            return self.answer
        self.rewrite_calls.append({"messages": messages,
                                   "options": dict(options or {}), "think": think})
        return self.rewrite


class BoomRewriteClient(FakeChatClient):
    """改写调用必抛异常（模拟改写模型不可用），生成调用不受影响。"""

    def chat(self, messages, stream_cb=None, options=None, think=False):
        if stream_cb:
            return super().chat(messages, stream_cb=stream_cb,
                                options=options, think=think)
        raise RuntimeError("改写模型调用失败")


def _ask_with(fake, query, **kw):
    """打桩对话模型（chat 可用、向量通道关闭走 FTS 单路）后执行 rag.ask。"""
    orig = (llm.chat_available, llm.get_chat_client, llm.embed_available)
    llm.chat_available = lambda: True
    llm.get_chat_client = lambda: fake
    llm.embed_available = lambda: False
    try:
        return rag.ask(query, **kw)
    finally:
        (llm.chat_available, llm.get_chat_client, llm.embed_available) = orig


def _by_file(result, suffix):
    for s in result["sources"]:
        if s["file"].endswith(suffix):
            return s
    return None


def _seed():
    db.init_db()
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO kb_folders(path, added_at) VALUES('kb', 0)")

        def add_file(path, chunks):
            cur = conn.execute(
                "INSERT INTO kb_files(folder_id, path, mtime, size) VALUES(1, ?, 0, 0)",
                (path,))
            for seq, text in enumerate(chunks):
                conn.execute(
                    "INSERT INTO chunks(file_id, seq, text, btext, vec) "
                    "VALUES(?, ?, ?, ?, NULL)",
                    (cur.lastrowid, seq, text, db.chunk_btext(text)))

        add_file(_DOC_A, [_CHUNK_FEE, _CHUNK_SIGN])
        add_file("报销制度.md", [_CHUNK_MD])
        add_file("季度汇报.pptx", [_CHUNK_PPT])
        add_file("报价单.xlsx", [_CHUNK_XLSX])
        conn.commit()
    finally:
        conn.close()


def test_helpers():
    print("\n[1] 前缀/定位/引用解析纯函数")
    t, b = rag._split_prefix("a.md · 标题 X\n正文")
    check("前缀拆出所在标题", t == "标题 X" and b == "正文", repr((t, b)))
    t, b = rag._split_prefix("a.md\n正文一行")
    check("仅文件名前缀标题为空", t == "" and b == "正文一行", repr((t, b)))
    t, b = rag._split_prefix("单行无前缀")
    check("单行块视为无前缀", t == "" and b == "单行无前缀", repr((t, b)))
    check("页标记定位", rag._locator("x · [第12页]\n内容") == "第12页")
    check("工作表标记定位", rag._locator("正文 [工作表 报价]\n内容") == "工作表 报价")
    check("无标记定位为空", rag._locator("普通正文") == "")
    check("引用编号越界过滤", rag._parse_citations("答 来源 [1][5]", 2) == [1])
    check("无来源标记为空", rag._parse_citations("没有引用", 2) == [])


def test_rewrite_used():
    print("\n[2] 有 history：检索用改写后的 query")
    fake = FakeChatClient(rewrite=REWRITE_TEXT, answer="经办人是周敏。 来源 [1]")
    toks = []
    r = _ask_with(fake, "经手人是谁", stream_cb=toks.append, history=_HISTORY)

    check("改写词命中周敏块（原词必落空）",
          any("周敏" in s["snippet"] for s in r["sources"]),
          str([s["snippet"] for s in r["sources"]]))
    check("rewritten_query 为改写文案", r["rewritten_query"] == REWRITE_TEXT,
          repr(r["rewritten_query"]))
    check("走 FTS 检索通道", r["mode"] == "fts", r["mode"])
    check("改写调用恰好一次", len(fake.rewrite_calls) == 1, str(len(fake.rewrite_calls)))
    call = fake.rewrite_calls[0]
    check("改写为非流式小调用（温度 0 / 60 token / think 关）",
          call["options"].get("temperature") == 0
          and call["options"].get("num_predict") == 60
          and call["think"] is False, str(call["options"]))
    msgs = call["messages"]
    check("改写消息 = system + 两轮历史 + 新问题",
          [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
          and msgs[0]["content"] == rag.REWRITE_SYSTEM
          and msgs[1]["content"] == _HISTORY[0]["content"]
          and msgs[2]["content"] == _HISTORY[1]["content"]
          and msgs[3]["content"] == "经手人是谁", str(msgs)[:200])
    check("生成仍流式回调", fake.answer_calls == 1 and toks == ["经办人是周敏。 来源 [1]"],
          str(toks))
    gen_user = fake.answer_messages[0][-1]["content"]
    check("生成上下文来自改写后检索（含周敏块）", "周敏" in gen_user)
    check("生成问题保持原 query", gen_user.endswith("问题：经手人是谁"),
          repr(gen_user[-40:]))


def test_rewrite_fallbacks():
    print("\n[3] 改写失败一律回退原 query")
    fake = BoomRewriteClient(answer="按 T+7 结算。 来源 [1]")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None, history=_HISTORY)
    check("改写异常时问答不受影响", r["answer"] == "按 T+7 结算。 来源 [1]")
    check("改写异常回退原 query", r["rewritten_query"] == "运费怎么结算",
          repr(r["rewritten_query"]))
    check("异常时仍命中并引用", r["cited"] == [1] and len(r["sources"]) == 1,
          str(r["cited"]))
    check("异常时生成调用一次", fake.answer_calls == 1)

    fake = FakeChatClient(rewrite="   ", answer="x")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None, history=_HISTORY)
    check("空改写回退原 query", r["rewritten_query"] == "运费怎么结算")

    fake = FakeChatClient(rewrite="蓝" * 101, answer="x")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None, history=_HISTORY)
    check("超长改写回退原 query", r["rewritten_query"] == "运费怎么结算")

    fake = FakeChatClient(rewrite="「" + REWRITE_TEXT + "」\n解释说明", answer="x")
    r = _ask_with(fake, "经手人是谁", stream_cb=lambda t: None, history=_HISTORY)
    check("裹引号多行输出取首行剥引号", r["rewritten_query"] == REWRITE_TEXT,
          repr(r["rewritten_query"]))


def test_no_history():
    print("\n[4] 无 history：不触发改写调用")
    fake = FakeChatClient(rewrite=REWRITE_TEXT, answer="x")
    toks = []
    r = _ask_with(fake, "经手人是谁", stream_cb=toks.append)
    check("无改写调用", fake.rewrite_calls == [], str(fake.rewrite_calls))
    check("原词落空走未检索到提示", "没有检索到" in r["answer"], r["answer"][:50])
    check("rewritten_query 等于原 query", r["rewritten_query"] == "经手人是谁")
    check("落空早退不触发生成", fake.answer_calls == 0 and toks == [r["answer"]],
          f"answer_calls={fake.answer_calls}, toks={toks[:1]}")
    check("早退路径 cited 为空", r["cited"] == [])

    fake = FakeChatClient(rewrite=REWRITE_TEXT, answer="按 T+7。 来源 [1]")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None)
    check("能命中时同样无改写直答",
          fake.rewrite_calls == [] and fake.answer_calls == 1)
    check("cited 校验通过", r["cited"] == [1], str(r["cited"]))


def test_title_locator():
    print("\n[5] sources 的 title/locator 与 snippet 去前缀")
    fake = FakeChatClient(answer="x")

    r = _ask_with(fake, "一线城市住宿标准多少钱", stream_cb=lambda t: None)
    s = _by_file(r, "报销制度.md")
    check("md 块 title=所在标题", s and s["title"] == "差旅报销", str(s))
    check("md 块无 locator", s and s["locator"] == "")
    check("snippet 去掉前缀行", s and s["snippet"] == "一线城市住宿每晚 500 元。",
          repr(s and s["snippet"]))

    r = _ask_with(fake, "第三季度营收毛利率怎么样", stream_cb=lambda t: None)
    s = _by_file(r, "季度汇报.pptx")
    check("页标记解析 locator", s and s["locator"] == "第3页", str(s))
    check("页标记所在标题原样保留", s and s["title"] == "[第3页]", repr(s and s["title"]))

    r = _ask_with(fake, "报价单里的产品金额", stream_cb=lambda t: None)
    s = _by_file(r, "报价单.xlsx")
    check("工作表标记解析 locator", s and s["locator"] == "工作表 报价", str(s))
    check("无标题前缀 title 为空", s and s["title"] == "", repr(s and s["title"]))
    check("工作表标记保留在 snippet", s and s["snippet"].startswith("[工作表 报价]"),
          repr(s and s["snippet"]))


def test_cited():
    print("\n[6] cited 引用编号校验")
    fake = FakeChatClient(answer="按 T+7 结算。来源 [0][1][2][9]")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None)
    check("零号与越界编号被过滤", r["cited"] == [1], str(r["cited"]))

    fake = FakeChatClient(answer="这个知识库里没有相关内容。")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None)
    check("无来源标记 cited 为空", r["cited"] == [], str(r["cited"]))

    fake = FakeChatClient(answer="细节见来源 [1][1]")
    r = _ask_with(fake, "运费怎么结算", stream_cb=lambda t: None)
    check("重复编号去重", r["cited"] == [1], str(r["cited"]))


def test_list_intent():
    print("\n[7] 清单意图不触发改写（行为保持不变）")
    fake = FakeChatClient(rewrite=REWRITE_TEXT, answer="x")
    r = _ask_with(fake, "知识库里有哪些文件", stream_cb=lambda t: None, history=_HISTORY)
    check("清单意图不调改写", fake.rewrite_calls == [], str(fake.rewrite_calls))
    check("清单意图返回 list 模式", r["mode"] == "list", r["mode"])
    check("rewritten_query 等于原 query", r["rewritten_query"] == "知识库里有哪些文件")
    check("sources/cited 均为空", r["sources"] == [] and r["cited"] == [])


def main():
    try:
        _seed()
        test_helpers()
        test_rewrite_used()
        test_rewrite_fallbacks()
        test_no_history()
        test_title_locator()
        test_cited()
        test_list_intent()
        print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
              f"({PASS} passed, {FAIL} failed)")
        return 1 if FAIL else 0
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
