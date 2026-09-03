"""本轮升级功能测试：批量 embed、批量 AI 分类、自动知识库、图片语义、模型管理、自启。"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, fileindex
from backend.knowledge import indexer, imgsearch, rag
from backend.core import classifier
from backend.ollama_client import OllamaClient

PASS = 0
FAIL = 0
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".test_tmp")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    db.init_db()
    fileindex.ensure_default_roots()
    os.makedirs(BASE, exist_ok=True)
    client = OllamaClient()
    st = client.status()
    if not st["running"]:
        print("Ollama 未运行，跳过 AI 部分测试")
        return 0

    # 清理历史残留
    for r in fileindex.list_roots(only_enabled=False):
        if os.path.basename(r["path"]).startswith("fb_up_"):
            fileindex.remove_root(r["id"])
            shutil.rmtree(r["path"], ignore_errors=True)

    # ---------- 1. 批量 embed ----------
    if st["embed_ready"]:
        texts = [f"测试文本第{i}段：人工智能文件整理助手" for i in range(35)]
        t0 = time.time()
        vecs = client.embed(texts)
        t1 = time.time() - t0
        check("batch embed 35 texts", len(vecs) == 35 and len(vecs[0]) > 100,
              f"{len(vecs)} vecs, {t1:.1f}s")
        print(f"       (35 段耗时 {t1:.1f}s，旧版逐段约需 {t1 * 6:.0f}s+)")

    # ---------- 2. 批量 AI 分类 ----------
    sb = os.path.join(BASE, "fb_up_classify")
    shutil.rmtree(sb, ignore_errors=True)
    os.makedirs(sb)
    cases = [
        ("安装包_xyz.exe", "安装包", b"MZ\x90\x00" + b"\x00" * 50),
        ("vacation_photo", "图片", b"\xff\xd8\xff\xe1jpeg"),
        ("读书笔记_2024", "文档", "这本书第三章讲了文件整理的方法论".encode("utf-8")),
        ("data_export", "数据", b'{"rows": [1,2,3]}'),
        ("main_script", "代码", b"import os\nprint('hi')"),
        ("压缩_backup", "压缩包", b"PK\x03\x04zipdata"),
    ]
    files = []
    for name, exp, content in cases:
        p = os.path.join(sb, name)
        with open(p, "wb") as f:
            f.write(content)
        files.append({"path": p, "name": name, "ext": os.path.splitext(name)[1].lstrip(".").lower(),
                      "size": len(content), "mtime": time.time()})
    db.set_setting("ai_classify_cache", "{}")  # 清缓存测真实批量
    t0 = time.time()
    result = classifier.ai_classify(files)
    t1 = time.time() - t0
    hits = sum(1 for name, exp, _ in cases if result[os.path.join(sb, name)][0] == exp)
    check("batch AI classify", hits >= 4, f"{hits}/{len(cases)} 正确, {t1:.1f}s")
    print(f"       (6 个文件一次批量调用 {t1:.1f}s)")

    # ---------- 3. 自动知识库：sync + index_files ----------
    kb_root = os.path.join(BASE, "fb_up_kb")
    shutil.rmtree(kb_root, ignore_errors=True)
    os.makedirs(kb_root)
    doc = os.path.join(kb_root, "产品说明.md")
    with open(doc, "w", encoding="utf-8") as f:
        f.write("# FileButler\n完全本地的文件管家，支持语义搜索和智能问答，无需 API Key。\n")

    fileindex.add_root(kb_root)
    added, removed = indexer.sync_auto_folders()
    folders = {f["path"]: f for f in indexer.list_folders()}
    auto_folder = folders.get(kb_root)
    check("sync creates auto folder", auto_folder is not None and auto_folder["source"] == "auto")
    check("sync idempotent", indexer.sync_auto_folders() == ([], []))

    # index_files 增量索引单文件
    r = indexer.index_files([doc])
    check("index_files indexes doc", r.get("indexed") == 1, str(r))
    r2 = indexer.index_files([doc])
    check("index_files skips unchanged", r2.get("skipped") == 1, str(r2))

    # 搜索验证
    sr = rag.search("文件管家支持什么", k=3)
    top = sr["results"][0] if sr["results"] else None
    check("auto kb searchable", top is not None and top["file_path"] == doc, str(sr)[:120])

    # 删除同步
    os.remove(doc)
    indexer.remove_kb_file(doc)
    sr = rag.search("文件管家支持什么", k=3)
    check("kb removed after delete", all(r2_["file_path"] != doc for r2_ in sr["results"]))

    # 移除 watch root → auto folder 级联删除
    rid = next(r["id"] for r in fileindex.list_roots(False) if r["path"] == kb_root)
    fileindex.remove_root(rid)
    indexer.sync_auto_folders()
    folders = {f["path"] for f in indexer.list_folders()}
    check("auto folder removed with root", kb_root not in folders)

    # ---------- 4. 模型管理 ----------
    r = client.delete_model("totally-fake-model:0xdeadbeef")
    check("delete nonexistent model errors gracefully",
          r.get("ok") is False and "error" in r, str(r))

    # ---------- 5. 图片语义（vl 模型未装 → 提示路径） ----------
    r = imgsearch.describe_files([])
    check("describe_files empty ok", r == {"described": 0, "skipped": 0, "failed": 0})
    fake = os.path.join(BASE, "no_such.png")
    r = imgsearch.describe_files([fake])
    check("describe missing file skipped", r.get("described") == 0)

    # ---------- 6. 开机自启注册表 ----------
    from backend.api import Api
    api = Api()
    r = api.set_autostart(True)
    check("autostart enable", r.get("ok") is True)
    s = api.autostart_status()
    check("autostart status reflects", s["enabled"] is True and bool(s["target"]), str(s))
    r = api.set_autostart(False)
    s = api.autostart_status()
    check("autostart disable", s["enabled"] is False)

    shutil.rmtree(BASE, ignore_errors=True)
    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
