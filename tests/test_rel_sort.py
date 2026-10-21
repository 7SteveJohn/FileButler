"""文件搜索相关度排序测试（不需要 Ollama / 不依赖任何模型服务）。

覆盖（backend/fileindex.search）：
  1. 多词 AND 多命中：sort_by="relevance" 按 file_fts.rank（bm25）把更相关者
     排前，且与 mtime 排序可对照出差异
  2. 无文本条件 / 短词 LIKE 降级：relevance 无分数可比，回退 mtime
  3. sort_by 非法值：安全回退 mtime，不注入不报错

运行：.venv/Scripts/python tests/test_rel_sort.py
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_rel_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex

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


_BASE = 1_700_000_000.0
# 命中词「实验报告」「数据集」：「数据集」「实验报告」在 R2 各出现两次 →
# bm25 词频更高更相关；mtime 故意反向（R2 最旧），两种排序才对照得出差异
_R2 = "实验报告数据集实验报告数据集.txt"   # 各词×2，最相关，mtime 最旧
_R1 = "实验报告数据集说明.docx"           # 各词×1，mtime 居中
_N = "无关文件.jpg"                       # 不命中，mtime 最新
_FILLERS = 30  # 无关填充行：FTS5 bm25 的 IDF 在「词命中大半库」时被钳到极小值，
               # rank 全部坍缩为 -0.0（spike 实测），足量无关行才拉开分数差距


def _seed():
    """直接 INSERT 入库（触发器自动同步 file_fts），mtime 精确可控。"""
    rows = [
        (os.path.join(_SANDBOX, _R1), _R1, "docx", "文档", 100, _BASE + 200),
        (os.path.join(_SANDBOX, _R2), _R2, "txt", "文档", 100, _BASE + 100),
        (os.path.join(_SANDBOX, _N), _N, "jpg", "图片", 100, _BASE + 300),
    ]
    for i in range(_FILLERS):
        name = f"filler_{i:02d}.dat"
        rows.append((os.path.join(_SANDBOX, name), name, "dat", "其他", 1, _BASE + i))
    conn = db.get_conn()
    try:
        conn.executemany(
            "INSERT INTO file_index(path,name,ext,category,size,mtime) VALUES(?,?,?,?,?,?)",
            rows)
        conn.commit()
    finally:
        conn.close()


def _names(result):
    return [i["name"] for i in result["items"]]


def main():
    try:
        db.init_db()
        # file_index 表 + file_fts/触发器（init_db 的 SCHEMA 不含 file_index）
        fileindex.ensure_default_roots()
        db.ensure_fts()
        _seed()

        # ---------- 1. 多词 AND 多命中：relevance 把更相关者排前 ----------
        r = fileindex.search(query="实验报告 数据集")
        check("多词 AND 命中 2 个", r["total"] == 2, str(r))
        rel = _names(fileindex.search(query="实验报告 数据集", sort_by="relevance"))
        by_mtime = _names(fileindex.search(query="实验报告 数据集", sort_by="mtime"))
        check("relevance 把词频更高者排前", rel == [_R2, _R1], str(rel))
        check("mtime 排序与之相反（对照差异）", by_mtime == [_R1, _R2], str(by_mtime))
        check("relevance 忽略 sort_order（无字段级升降序）",
              _names(fileindex.search(query="实验报告 数据集", sort_by="relevance",
                                      sort_order="asc")) == [_R2, _R1])

        # ---------- 2. 无文本 / LIKE 降级 / 混合词：映射条件 ----------
        # 无文本时返回全索引（含填充行），mtime 降序的前三名必须仍是 N→R1→R2
        check("无文本条件 relevance 回退 mtime",
              _names(fileindex.search(sort_by="relevance"))[:3] == [_N, _R1, _R2])
        check("短词 LIKE 降级 relevance 回退 mtime",
              _names(fileindex.search(query="报告", sort_by="relevance")) == [_R1, _R2])
        # 混合分支（长词 FTS + 短词 LIKE）join_fts 非空 → relevance 仍映射 rank
        check("混合词 FTS 生效时 relevance 仍按 rank 排",
              _names(fileindex.search(query="实验报告 集", sort_by="relevance"))
              == [_R2, _R1])

        # ---------- 3. 非法 sort_by 安全回退 ----------
        evil = _names(fileindex.search(sort_by="rank; DROP TABLE file_index;--"))
        check("非法 sort_by 回退 mtime 且不报错", evil[:3] == [_N, _R1, _R2], str(evil))
        check("库未被破坏", fileindex.search()["total"] == _FILLERS + 3)

        # ---------- 4. 计数与分页不受影响 ----------
        pg = fileindex.search(query="实验报告 数据集", sort_by="relevance",
                              limit=1, offset=1)
        check("relevance 分页正常", pg["total"] == 2 and _names(pg) == [_R1], str(pg))
        check("计数未封顶", pg["total_capped"] is False, str(pg))

        print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} "
              f"({PASS} passed, {FAIL} failed)")
        return 1 if FAIL else 0
    finally:
        db.close_all_conns()
        shutil.rmtree(_SANDBOX, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
