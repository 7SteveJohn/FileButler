"""P0 spike：sqlite-vec 0.1.9 在本机环境的可行性验证（一次性验证脚本，可随时删除）。

背景：db.py 模块注释记录过「不依赖 sqlite-vec 扩展，打包更可靠」的早期决定，
本脚本用实证回答它是否还成立。对比对象是 VectorIndex 的等价数学
（numpy 暴力余弦，float32 / float16 两种精度）。

验证项：
  1. 扩展加载（独立连接各加载一次；WAL 模式共存）
  2. vec0 建表 + 批量插入 10 万 x 1024 维 float32
  3. KNN 查询延迟（k=8）对比 numpy 暴力余弦
  4. 结果正确性：与 float32 / float16 对拍的 top-8 重合率
  5. 单行 DELETE+INSERT 增量写延迟（对应现在 invalidate 全量重载的场景）
  6. 磁盘占用对比（vec0 表 vs chunks.vec BLOB 方案）

用法：.venv/Scripts/python tests/spikes/spike_sqlite_vec.py [N]
N 默认 100000。全程使用临时目录，不触碰真实数据库。
"""
import os
import sqlite3
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DIM = 1024
K = 8
N_QUERIES = 20

SQL_CREATE = "CREATE VIRTUAL TABLE IF NOT EXISTS vec_docs USING vec0(embedding float[%d])" % DIM
SQL_INSERT = "INSERT INTO vec_docs(rowid, embedding) VALUES(?, ?)"
SQL_KNN_PARAM = ("SELECT rowid, distance FROM vec_docs "
                 "WHERE embedding MATCH ? AND k = ? ORDER BY distance")
SQL_DELETE_ONE = "DELETE FROM vec_docs WHERE rowid = ?"
SQL_COUNT = "SELECT COUNT(*) AS c FROM vec_docs"


def make_conn(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def normalized(rng, n):
    m = rng.standard_normal((n, DIM)).astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def brute_scores(matrix16, q16):
    return matrix16 @ q16


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    rng = np.random.default_rng(42)
    out = {}

    print(f"[spike] sqlite-vec 可行性验证 N={n} dim={DIM}")
    try:
        import sqlite_vec
        print(f"[spike] sqlite-vec 版本: {sqlite_vec.__version__ if hasattr(sqlite_vec, '__version__') else '0.1.x'}")
    except ImportError as e:
        print("NO-GO: sqlite-vec 未安装", e)
        return 1

    workdir = tempfile.mkdtemp(prefix="fb_captest_spike_")
    db_path = os.path.join(workdir, "spike.db")

    # ---- 1. 加载 + WAL 共存 + 双连接 ----
    t0 = time.perf_counter()
    conn = make_conn(db_path)
    conn.execute(SQL_CREATE)
    conn.commit()
    conn2 = make_conn(db_path)
    n_tables = conn2.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE name='vec_docs'").fetchone()["c"]
    print(f"[1] 扩展加载 + WAL + 双连接可见: OK ({(time.perf_counter()-t0)*1000:.1f} ms, "
          f"conn2 可见表数={n_tables})")
    out["load_ok"] = True

    # ---- 2. 批量插入 ----
    corpus = normalized(rng, n)
    t0 = time.perf_counter()
    conn.executemany(SQL_INSERT,
                     ((i, corpus[i].tobytes()) for i in range(n)))
    conn.commit()
    ins_s = time.perf_counter() - t0
    print(f"[2] 插入 {n} 条: {ins_s:.2f}s ({n/ins_s:.0f} rows/s)")
    out["insert_s"] = round(ins_s, 2)

    # ---- 3. KNN 延迟 vs numpy 暴力 ----
    queries = normalized(rng, N_QUERIES)
    t0 = time.perf_counter()
    for qi in range(N_QUERIES):
        rows = conn.execute(SQL_KNN_PARAM,
                            (queries[qi].tobytes(), K)).fetchall()
        assert len(rows) == K, f"KNN 应返回 {K} 行: {len(rows)}"
    vec_ms = (time.perf_counter() - t0) / N_QUERIES * 1000
    print(f"[3] vec0 KNN(k={K}): {vec_ms:.2f} ms/次")
    out["vec0_knn_ms"] = round(vec_ms, 2)

    ids = np.arange(n, dtype=np.int64)
    mat32 = corpus
    q0 = queries[0]
    t0 = time.perf_counter()
    for qi in range(N_QUERIES):
        s = mat32 @ queries[qi]
        np.argpartition(-s, K - 1)[:K]
    np32_ms = (time.perf_counter() - t0) / N_QUERIES * 1000
    mat16 = mat32.astype(np.float16)
    t0 = time.perf_counter()
    for qi in range(N_QUERIES):
        s = brute_scores(mat16, queries[qi].astype(np.float16))
        np.argpartition(-s.astype(np.float32), K - 1)[:K]
    np16_ms = (time.perf_counter() - t0) / N_QUERIES * 1000
    print(f"    numpy float32 暴力: {np32_ms:.2f} ms/次 | float16 暴力: {np16_ms:.2f} ms/次")
    out["np32_ms"] = round(np32_ms, 2)
    out["np16_ms"] = round(np16_ms, 2)

    # ---- 4. 正确性对拍（全量 20 query 的 top-8 重合率）----
    overlaps32, overlaps16 = [], []
    mat16n = mat16 / np.linalg.norm(mat16.astype(np.float32), axis=1, keepdims=True)
    for qi in range(N_QUERIES):
        rows = conn.execute(SQL_KNN_PARAM,
                            (queries[qi].tobytes(), K)).fetchall()
        vec_set = {r["rowid"] for r in rows}
        s32 = mat32 @ queries[qi]
        top32 = set(np.argpartition(-s32, K - 1)[:K].tolist())
        s16 = brute_scores(mat16n, (queries[qi] / np.linalg.norm(queries[qi])).astype(np.float16))
        top16 = set(np.argpartition(-s16.astype(np.float32), K - 1)[:K].tolist())
        overlaps32.append(len(vec_set & top32) / K)
        overlaps16.append(len(vec_set & top16) / K)
    print(f"[4] top-{K} 重合率: vs float32 {np.mean(overlaps32)*100:.1f}% | "
          f"vs float16(现网精度) {np.mean(overlaps16)*100:.1f}%")
    out["overlap32"] = round(float(np.mean(overlaps32)), 4)
    out["overlap16"] = round(float(np.mean(overlaps16)), 4)

    # ---- 5. 单行增量写 ----
    rid = int(conn.execute(SQL_COUNT).fetchone()["c"]) - 1
    new_vec = normalized(rng, 1)[0]
    t0 = time.perf_counter()
    conn.execute(SQL_DELETE_ONE, (rid,))
    conn.execute(SQL_INSERT, (rid, new_vec.tobytes()))
    conn.commit()
    upd_ms = (time.perf_counter() - t0) * 1000
    print(f"[5] 单行 DELETE+INSERT: {upd_ms:.2f} ms")
    out["update_ms"] = round(upd_ms, 2)

    # ---- 6. 占用 ----
    db_bytes = os.path.getsize(db_path)
    blob_equiv = n * DIM * 4
    print(f"[6] 库文件 {db_bytes/1048576:.1f} MB（BLOB 方案纯向量当量 "
          f"{blob_equiv/1048576:.1f} MB）")
    out["db_mb"] = round(db_bytes / 1048576, 1)

    conn.close()
    conn2.close()

    ok = out["overlap16"] >= 0.75 and out["vec0_knn_ms"] < 50
    print("\n[结论] " + ("GO —— sqlite-vec 可进入 P2 实装" if ok else "NO-GO —— 见上方数据"))
    print("SPIKE_JSON: " + str(out).replace("'", '"'))
    import shutil
    shutil.rmtree(workdir, ignore_errors=True)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
