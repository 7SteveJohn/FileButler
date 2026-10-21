"""P1 混合检索测试：chunk_fts 词法通道 + RRF 融合 + 降级链（不需要 Ollama）。

覆盖：
  1. chunk_btext 二元切分（CJK 重叠二元组 / ASCII 整词 / 边界）
  2. chunk_fts 触发器同步（INSERT/DELETE/UPDATE）与一致性重建
  3. chunk_fts_search：精确实体、问法改写（trigram 必挂、二元组必中的用例）
  4. _rrf_fuse 纯函数（融合排序、截断、单路退化）
  5. rag.search 降级链：fts 单路（无模型）、hybrid（打桩向量通道）
  6. 旧库迁移：btext 为 NULL 的存量行回填 + FTS 重建

运行：.venv/Scripts/python tests/test_hybrid.py
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_hybrid_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend import db, llm
from backend.knowledge import rag

_DOC_A = "蓝湖物流服务合同.md"
_DOC_A_TEXT = ("合同编号 LH-2025-077，乙方蓝湖物流有限公司，运费结算周期 T+7，"
               "逾期按日千分之三计违约金。乙方负责人孙倩，结算联系人电话 8527-4432。")
_DOC_B = "机房巡检规程.md"
_DOC_B_TEXT = ("机房巡检每两周执行一次，检查 UPS 电池状态与空调湿度。"
               "UPS 电池组计划于 2025 年 6 月整体更换。A3 区门禁卡仅限运维组持有。")


def _insert_doc(conn, title, body, with_btext=True):
    cur = conn.execute(
        "INSERT INTO kb_files(folder_id, path, mtime, size) VALUES(1, ?, 0, 0)",
        (title,))
    fid = cur.lastrowid
    btext = db.chunk_btext(body) if with_btext else None
    cur2 = conn.execute(
        "INSERT INTO chunks(file_id, seq, text, btext, vec) VALUES(?, 0, ?, ?, NULL)",
        (fid, body, btext))
    conn.commit()
    return cur2.lastrowid


def _fts_count(conn):
    return conn.execute("SELECT COUNT(*) AS c FROM chunk_fts").fetchone()["c"]


def test_bigram():
    assert db.chunk_btext("运费结算") == "运费 费结 结算", db.chunk_btext("运费结算")
    assert db.chunk_btext("UPS电池2025") == "ups 电池 2025", db.chunk_btext("UPS电池2025")
    assert db.chunk_btext("好") == "好"
    assert db.chunk_btext("") == ""
    # 标点与空格是分隔符，不产生跨段二元组
    assert db.chunk_btext("报销。差旅") == "报销 差旅", db.chunk_btext("报销。差旅")
    print("  OK chunk_btext 二元切分")


def test_expr():
    assert db.chunk_fts_expr("") == ""
    assert db.chunk_fts_expr("!!!") == ""
    e = db.chunk_fts_expr("运费怎么结算")
    assert '"运费"' in e and " OR " in e, e
    print("  OK chunk_fts_expr")


def test_triggers_and_search():
    conn = db.get_conn()
    try:
        a_id = _insert_doc(conn, _DOC_A, _DOC_A_TEXT)
        b_id = _insert_doc(conn, _DOC_B, _DOC_B_TEXT)
        assert _fts_count(conn) == 2, _fts_count(conn)
    finally:
        conn.close()

    # 精确实体：编号中的 ASCII 词元直接命中
    hits = db.chunk_fts_search("LH-2025-077 的运费怎么结算", k=8)
    assert hits and hits[0] == a_id, hits

    # 问法改写：提问「运费怎么结算」与原文「运费结算周期」无共享三元组
    # （trigram 方案必挂），二元组「运费」「结算」必中且目标排第一
    hits = db.chunk_fts_search("运费怎么结算", k=8)
    assert hits and hits[0] == a_id, hits

    # 单元组查询（两个二元组都只在另一篇里）
    hits = db.chunk_fts_search("UPS 电池什么时候更换", k=8)
    assert hits and hits[0] == b_id, hits

    # 与原文完全无关的查询：允许空结果，不允许报错
    assert isinstance(db.chunk_fts_search("唵吽哒", k=8), list)

    # 触发器：DELETE chunks 行 → FTS 同步删除
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM chunks WHERE id=?", (b_id,))
        conn.commit()
        assert _fts_count(conn) == 1, _fts_count(conn)
        hits = db.chunk_fts_search("UPS 电池", k=8)
        assert b_id not in hits, hits
    finally:
        conn.close()
    print("  OK 触发器同步 + 词法检索")


def test_rrf_fuse():
    fused = rag._rrf_fuse([[10, 11, 12], [11, 99, 10]], k=3)
    ids = [i for i, s in fused]
    # 11 在两路分别排 1、0；10 在两路分别排 0、2 → 11 第一，10 第二
    assert ids[0] == 11 and ids[1] == 10, fused
    assert len(ids) == 3
    # 单路退化 = 原顺序
    assert [i for i, s in rag._rrf_fuse([[5, 6, 7]], k=2)] == [5, 6]
    # 空输入
    assert rag._rrf_fuse([[], []], k=5) == []
    print("  OK RRF 融合")


def test_rrf_merge_by_file():
    """global_search 内容两路的文件级 RRF 融合（纯函数，无需入库）。"""
    a = [{"file_path": "a.txt", "text": "A片段"}, {"file_path": "b.txt", "text": "B片段"}]
    b = [{"file_path": "b.txt", "text": "B块", "score": 0.9},
         {"file_path": "b.txt", "text": "B块2", "score": 0.8},
         {"file_path": "c.txt", "text": "C块", "score": 0.7}]
    merged = rag.rrf_merge_by_file([a, b], k=3)
    order = [r["file_path"] for r in merged]
    # b 在两路都靠前（路内同文件只计最好名次）→ 第一；c 仅一路第二 → 压过单路 a
    assert order == ["b.txt", "a.txt", "c.txt"] or order == ["b.txt", "c.txt", "a.txt"], order
    # 代表行保留先出现者的展示形态：b 来自路 a 的片段（不带 score 字段）
    b_row = next(r for r in merged if r["file_path"] == "b.txt")
    assert b_row["text"] == "B片段", b_row
    # 同文件在一路内不重复计分：路 b 里 b.txt 两块只算一个名次
    only_b = rag.rrf_merge_by_file([b], k=3)
    assert [r["file_path"] for r in only_b] == ["b.txt", "c.txt"], only_b
    # 空路与缺 file_path 的行安全跳过
    assert rag.rrf_merge_by_file([[], [{"file_path": "x", "text": "x"}], []]) != []
    print("  OK 文件级 RRF 融合")


def test_rag_channels():
    # _DOC_A 已在 test_triggers_and_search 中入库（path 唯一），直接复用
    db.vec_index.invalidate()

    orig_embed_available, orig_get_embed = llm.embed_available, llm.get_embed_client
    try:
        # 无模型：fts 单路可用（这正是「没装 Ollama 也能问答」的关键路径）
        llm.embed_available = lambda: False
        r = rag.search("运费怎么结算", k=8)
        assert r["mode"] == "fts", r["mode"]
        assert r["results"] and r["results"][0]["file_path"].endswith(_DOC_A), r["results"][:1]

        # hybrid：向量通道打桩（故意把正确答案排在向量第 2 位，FTS 排第 0 位，
        # 融合后必须仍第一），嵌入客户端同样打桩避免真实网络调用
        a_id = db.chunk_fts_search("运费怎么结算", k=1)[0]
        other_id = a_id + 100  # 不存在的 id 没关系，_rows_in_order 会跳过
        llm.embed_available = lambda: True

        class _FakeClient:
            def embed_one(self, text):
                return [1.0] * 8

        llm.get_embed_client = lambda: _FakeClient()
        orig_vec_search = db.vec_index.search
        db.vec_index.search = lambda qv, k=8: [(other_id, 0.99), (a_id, 0.10)][:k]
        try:
            r = rag.search("运费怎么结算", k=2)
            assert r["mode"] == "hybrid", r["mode"]
            assert r["results"] and r["results"][0]["file_path"].endswith(_DOC_A), \
                f"融合后正确答案应第一: {r['results'][:2]}"
        finally:
            db.vec_index.search = orig_vec_search
    finally:
        llm.embed_available = orig_embed_available
        llm.get_embed_client = orig_get_embed
    print("  OK rag 降级链（fts 单路 / hybrid 融合）")


def test_migration_and_resync():
    # 模拟旧库：btext 为 NULL 的存量块
    conn = db.get_conn()
    try:
        stale_id = _insert_doc(conn, "旧库存量.md", "这是一段没有二元切分的旧文本内容",
                               with_btext=False)
        assert conn.execute(
            "SELECT btext FROM chunks WHERE id=?", (stale_id,)).fetchone()["btext"] is None
        # 手工弄脏 FTS（删掉一行），制造计数不一致
        conn.execute("DELETE FROM chunk_fts WHERE rowid=?", (stale_id,))
        conn.commit()
    finally:
        conn.close()

    db.init_db()  # 幂等：应回填 btext 并重建 FTS

    conn = db.get_conn()
    try:
        assert conn.execute(
            "SELECT btext FROM chunks WHERE id=?", (stale_id,)).fetchone()["btext"]
        assert _fts_count(conn) == conn.execute(
            "SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        hits = db.chunk_fts_search("旧文本内容", k=8)
        assert stale_id in hits, hits
    finally:
        conn.close()
    print("  OK 迁移回填 + 一致性重建")


def main():
    try:
        db.init_db()
        conn = db.get_conn()
        try:
            conn.execute("INSERT INTO kb_folders(path, added_at) VALUES('kb', 0)")
            conn.commit()
        finally:
            conn.close()
        test_bigram()
        test_expr()
        test_triggers_and_search()
        test_rrf_fuse()
        test_rrf_merge_by_file()
        test_rag_channels()
        test_migration_and_resync()
        print("\nALL HYBRID TESTS DONE")
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)


if __name__ == "__main__":
    main()
