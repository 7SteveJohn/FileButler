"""知识库限定范围测试：只在一份文档/一个库文件夹里检索。

关键约束：
- scope 为空必须是全库（不能因为加了这个功能就把默认行为改坏）
- scope 有值但范围内没有已索引内容时返回空，而不是悄悄回退全库
- 向量通道与 FTS 通道、以及无模型时的 LIKE 降级通道，三路都要遵守范围
"""
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_scope_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                          # noqa: E402
from backend.knowledge import rag              # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def seed(conn, folder_id, path, text):
    conn.execute("INSERT INTO kb_folders(id, path, added_at, source) VALUES(?,?,?, 'manual')",
                 (folder_id, "D:/kb%d" % folder_id, 0))
    conn.execute("INSERT INTO kb_files(folder_id, path, mtime, size, status) "
                 "VALUES(?,?,?,?, 'ok')", (folder_id, path, 0, len(text)))
    fid = conn.execute("SELECT id FROM kb_files WHERE path=?", (path,)).fetchone()["id"]
    conn.execute("INSERT INTO chunks(file_id, seq, text, btext) VALUES(?,0,?,?)",
                 (fid, text, db.chunk_btext(text)))
    return fid


def main():
    db.init_db()
    conn = db.get_conn()
    try:
        fa = seed(conn, 1, "D:/kb1/合同.txt", "甲方应当在合同签署后支付预付款。")
        fb = seed(conn, 2, "D:/kb2/发票.txt", "发票号码与开票日期见右上角。")
        conn.commit()
    finally:
        conn.close()

    # 限流模型：embed 不可用 → 走向量缺席的 FTS / LIKE 通道
    rag.llm.embed_available = lambda: False

    # 1) 范围为空 = 全库
    check("空 scope 不限范围", db.chunk_ids_in_scope(None) is None)
    all_hits = db.chunk_fts_search("合同", k=10)
    check("空 scope 能搜到合同", len(all_hits) >= 1, str(all_hits))

    # 2) 限定文件夹：只命中该库
    hits_a = db.chunk_fts_search("支付", k=10, scope={"folder_id": 1})
    hits_b = db.chunk_fts_search("支付", k=10, scope={"folder_id": 2})
    check("限定库1命中", len(hits_a) >= 1, str(hits_a))
    check("限定库2不命中", hits_b == [], str(hits_b))

    # 3) 限定单个文件
    hits_f = db.chunk_fts_search("发票", k=10, scope={"path": "D:/kb2/发票.txt"})
    check("限定单文件命中", len(hits_f) >= 1, str(hits_f))

    # 4) 范围内的 chunk id 集合正确，范围不存在时为空集
    ids_a = db.chunk_ids_in_scope({"folder_id": 1})
    check("scope chunk 集合只含本库", ids_a and fa in ids_a and fb not in ids_a, str(ids_a))
    check("不存在的范围是空集", db.chunk_ids_in_scope({"folder_id": 99}) == set())

    # 5) 向量索引接口：allowed 为空集直接空，allowed 含本 chunk 才返回
    import numpy as np
    db.vec_index._ensure_loaded()
    db.vec_index._ids = np.array([fa, fb], dtype=np.int64)
    db.vec_index._matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    v = db.vec_index.search([1.0, 0.0], k=5, allowed={fb})
    check("向量通道遵守 allowed", [i for i, _ in v] == [fb], str(v))
    check("向量通道 allowed 空集", db.vec_index.search([1.0, 0.0], k=5, allowed=set()) == [])
    v_all = db.vec_index.search([1.0, 0.0], k=5)
    check("向量通道不传 allowed 仍全量", len(v_all) == 2, str(v_all))

    # 6) rag.search：结果全部落在限定库内
    r = rag.search("支付 合同", k=5, scope={"folder_id": 1})
    paths = [x["file_path"] for x in r["results"]]
    check("rag.search 结果只在库1", paths and all("kb1" in p for p in paths), str(paths))
    r2 = rag.search("支付 合同", k=5, scope={"folder_id": 2})
    check("限定无内容库结果为空", r2["results"] == [], str(r2["results"]))

    # 7) 无模型时的 LIKE 降级也守范围（单字查询不产生 FTS 二元组）
    r3 = rag.search("甲", k=5, scope={"folder_id": 1})
    check("LIKE 降级守住范围", [x["file_path"] for x in r3["results"]] and
          all("kb1" in x["file_path"] for x in r3["results"]), str(r3["results"]))
    r4 = rag.search("甲", k=5, scope={"folder_id": 2})
    check("LIKE 降级不越界", r4["results"] == [], str(r4["results"]))

    # 8) 全库检索仍能看到两份文件
    r5 = rag.search("支付 发票", k=10)
    allpk = {x["file_path"] for x in r5["results"]}
    check("全库仍能跨库命中", "D:/kb1/合同.txt" in allpk or "D:/kb2/发票.txt" in allpk, str(allpk))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
