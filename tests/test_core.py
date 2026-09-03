"""核心引擎离线自测：不依赖 Ollama，验证扫描/规则/计划/执行/撤销/去重/切块。"""
import os
import shutil
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core import dedupe, executor, planner, rules, scanner
from backend.db import DB_PATH
from backend.knowledge import chunker, parsers

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
    sandbox = tempfile.mkdtemp(prefix="fb_test_")
    src = os.path.join(sandbox, "source")
    out = os.path.join(sandbox, "sorted")
    os.makedirs(os.path.join(src, "sub"))

    # 构造测试文件
    files = {
        "报告.pdf": b"%PDF-1.4 test",
        "笔记.md": b"# hello\nworld",
        "照片.jpg": b"\xff\xd8\xff\xe0jpgdata",
        "setup.exe": b"MZ\x90\x00fake",
        "archive.zip": b"PK\x03\x04zip",
        "data.json": b'{"a":1}',
        "unknownfile": b"\x00\x01binary mystery",
        os.path.join("sub", "deep.py"): b"print(1)",
    }
    for rel, content in files.items():
        p = os.path.join(src, rel)
        with open(p, "wb") as f:
            f.write(content)

    # 1. 扫描
    scanned = scanner.scan_folder(src)
    check("scan finds all files", len(scanned) == len(files), f"got {len(scanned)}")
    check("scan collects ext", any(f["ext"] == "pdf" for f in scanned))

    # 2. 规则
    check("pdf rule", rules.classify_by_ext("pdf") == ("文档", "PDF"))
    check("exe rule", rules.classify_by_ext("exe") == ("安装包", ""))
    check("unknown ext -> None", rules.classify_by_ext("zzz9") is None)

    # 3. 计划
    plan = planner.build_plan(scanned, out, by_year_images=False)
    moves = [r for r in plan if r["action"] == "move"]
    check("plan has moves", len(moves) == len(files), f"{len(moves)}")
    dst_cats = {r["category"] for r in moves}
    check("plan categories", {"文档", "图片", "安装包", "压缩包", "数据", "代码", "其他"} <= dst_cats, str(dst_cats))
    pdf_dst = next(r["dst"] for r in moves if r["src"].endswith("报告.pdf"))
    check("pdf target under 文档/PDF", "文档" in pdf_dst and "PDF" in pdf_dst, pdf_dst)

    # 4. 执行
    result = executor.execute(moves)
    check("execute all ok", all(r["ok"] for r in result["results"]) and len(result["results"]) == len(files))
    check("file actually moved", not os.path.exists(os.path.join(src, "报告.pdf")))
    check("file at target", os.path.exists(os.path.join(out, "文档", "PDF", "报告.pdf")))

    # 5. 撤销
    undo = executor.undo(result["batch_id"])
    check("undo all", undo["undone"] == len(files) and undo["failed"] == 0, str(undo))
    check("file restored", os.path.exists(os.path.join(src, "报告.pdf")))
    check("sorted dir emptied", not any(os.scandir(os.path.join(out, "文档", "PDF"))))

    # 5.5 重做（取消撤销）→ 再撤销，验证往返一致
    redo = executor.redo(result["batch_id"])
    check("redo all", redo["redone"] == len(files) and redo["failed"] == 0, str(redo))
    check("file back at target", os.path.exists(os.path.join(out, "文档", "PDF", "报告.pdf")))
    check("src empty again", not os.path.exists(os.path.join(src, "报告.pdf")))
    undo2 = executor.undo(result["batch_id"])
    check("undo again after redo", undo2["undone"] == len(files) and undo2["failed"] == 0)
    check("file restored again", os.path.exists(os.path.join(src, "报告.pdf")))

    # 6. 执行一次「整理到 src 自身」→ 重扫 → 再次生成计划应全部 skip（已在正确位置）
    scanned2 = scanner.scan_folder(src)
    plan2 = planner.build_plan(scanned2, src, by_year_images=False)
    result2 = executor.execute([r for r in plan2 if r["action"] == "move"])
    check("self-organize executes", all(r["ok"] for r in result2["results"]))
    scanned3 = scanner.scan_folder(src)
    plan3 = planner.build_plan(scanned3, src, by_year_images=False)
    skip_n = sum(1 for r in plan3 if r["action"] == "skip")
    move_n = sum(1 for r in plan3 if r["action"] == "move")
    check("replan skips in-place files", move_n == 0 and skip_n == len(scanned3),
          f"skip={skip_n} move={move_n} total={len(scanned3)}")
    executor.undo(result2["batch_id"])  # 还原，不影响后续测试

    # 7. 重复检测
    dup_dir = os.path.join(sandbox, "dups")
    os.makedirs(dup_dir)
    for n in ["a.txt", "b.txt"]:
        with open(os.path.join(dup_dir, n), "wb") as f:
            f.write(b"same content " * 100)
    with open(os.path.join(dup_dir, "c.txt"), "wb") as f:
        f.write(b"different")
    dscanned = scanner.scan_folder(dup_dir)
    groups = dedupe.find_duplicates(dscanned)
    check("dedupe finds 1 group", len(groups) == 1 and len(groups[0]["paths"]) == 2, str(groups))

    # 8. 切块器
    text = "\n".join(f"段落{i}，" + "内容" * 30 for i in range(20))
    chunks = chunker.chunk_text(text, target=200, overlap=40)
    check("chunker splits", len(chunks) > 5, f"{len(chunks)}")
    check("chunk sizes bounded", all(len(c) <= 420 for c in chunks))

    # 9. 解析器（纯文本路径）
    p = parsers.parse_file(os.path.join(src, "笔记.md"))
    check("md parse", p and "hello" in p)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    shutil.rmtree(sandbox, ignore_errors=True)
    # 清理本测试写入的操作日志，避免污染用户的真实历史
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM operations WHERE src LIKE ?", (f"{sandbox}%",))
        conn.commit()
    finally:
        conn.close()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
