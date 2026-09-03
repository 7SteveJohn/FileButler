"""知识库端到端测试：解析→切块→索引→语义搜索→RAG 问答（需 Ollama + bge-m3）。"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db
from backend.knowledge import indexer, rag

SANDBOX = tempfile.mkdtemp(prefix="fb_kb_")
KB = os.path.join(SANDBOX, "kb_docs")


def setup_docs():
    os.makedirs(KB)
    with open(os.path.join(KB, "公司制度.md"), "w", encoding="utf-8") as f:
        f.write("""# 公司报销制度

## 差旅报销
员工出差需提前三个工作日在 OA 系统提交出差申请。报销单需在出差返回后 15 天内提交，
逾期不予受理。住宿标准：一线城市每晚 500 元，其他城市每晚 400 元。

## 服务器管理
生产服务器只能通过跳板机访问，禁止直接 SSH。数据库密码每 90 天强制更换一次。
所有上线操作必须在变更管理平台留痕。
""")
    with open(os.path.join(KB, "产品规划.txt"), "w", encoding="utf-8") as f:
        f.write("""FileButler 产品规划 v2

第三季度目标：完成智能分类引擎，支持模糊文件归类。
第四季度目标：上线图片语义搜索，接入本地视觉模型。
竞品分析：Everything 只做文件名检索，不做内容理解；我们做内容级整理。
""")
    with open(os.path.join(KB, "ignored.bin"), "wb") as f:
        f.write(b"\x00\x01\x02not a document")


def main():
    setup_docs()
    db.init_db()
    folder = indexer.add_folder(KB)

    t0 = time.time()
    result = indexer.index_folder(folder["id"])
    print(f"索引结果: {result}  耗时 {time.time()-t0:.1f}s")
    assert result.get("indexed") == 2, f"应索引 2 个文档: {result}"
    assert result.get("failed", 0) <= 1, f"bin 文件解析失败可接受: {result}"

    # 增量：再索引应全部跳过
    t0 = time.time()
    result2 = indexer.index_folder(folder["id"])
    print(f"增量索引: {result2}  耗时 {time.time()-t0:.1f}s")
    assert result2.get("skipped") == 2, f"增量应跳过: {result2}"

    # 语义搜索：用同义词验证（搜索词与文档用词不同）
    for q, expect_file in [
        ("坐火车出差的钱怎么报销", "公司制度.md"),
        ("竞争对手产品对比分析", "产品规划.txt"),
        ("登录服务器有什么规定", "公司制度.md"),
    ]:
        r = rag.search(q, k=3)
        top = r["results"][0] if r["results"] else None
        ok = top and top["file_path"].endswith(expect_file)
        print(f"  {'OK ' if ok else 'MISS'} 「{q}」-> {top['file_path'].split(chr(92))[-1] if top else None} "
              f"(score={top['score'] if top else '-'}, mode={r['mode']})")

    # RAG 问答
    answer = rag.ask("出差住宿每晚最多能报多少钱？")
    print(f"\nRAG 回答: {answer['answer'][:300]}")
    print(f"来源数: {len(answer['sources'])}")
    assert "500" in answer["answer"] or "四百" in answer["answer"] or "400" in answer["answer"], "应答出住宿标准金额"

    print("\nALL RAG TESTS DONE")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    # 清理测试数据
    try:
        indexer.remove_folder(folder["id"])
    except Exception:
        pass


if __name__ == "__main__":
    main()
