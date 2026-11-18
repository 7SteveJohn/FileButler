"""v1.4.5 维护与增强回归套件：覆盖热键、自定义规则、路径前缀安全、目录搬移、
混合检索多维向量、分块器页码保留、RAG 列表意图、模板/大小写重命名、
音视频 HTTP Range 与压缩包预览、待清理隔离区管理、双语键对称性。"""
import io
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_maint145_")
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import api as api_mod, db, fileindex, hotkey, imgdecode, llm, model_rec, thumbserver, watcher  # noqa: E402
from backend.core import cleanup, executor, planner, report, rules, simimg  # noqa: E402
from backend.knowledge import chunker, imgsearch, rag  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _extract_js_keys(js_text):
    """轻量解析 zh-CN.js / en.js 的层级键集合（不引 JS 运行时）。"""
    # 去掉单行注释与单引号字符串字面量，提取 obj: { 与 key: 位置
    cleaned = []
    i, n = 0, len(js_text)
    while i < n:
        ch = js_text[i]
        if ch == "'" or ch == '"':
            quote = ch
            i += 1
            while i < n:
                if js_text[i] == "\\":
                    i += 2
                    continue
                if js_text[i] == quote:
                    i += 1
                    break
                i += 1
            cleaned.append("''")
        elif js_text[i:i + 2] == "//":
            while i < n and js_text[i] != "\n":
                i += 1
        else:
            cleaned.append(ch)
            i += 1
    src = "".join(cleaned)
    stack = []
    paths = set()
    token_re = re.compile(r"([A-Za-z_]\w*)\s*:\s*(\{?)|(\})")
    for m in token_re.finditer(src):
        key, brace_open, brace_close = m.group(1), m.group(2), m.group(3)
        if brace_close:
            if stack:
                stack.pop()
        elif brace_open == "{":
            stack.append(key)
        elif key:
            paths.add(".".join([*stack, key]))
    return paths


def main():
    db.init_db()
    fileindex.ensure_default_roots()
    db.init_db()   # file_index 建好后补齐 file_fts 虚拟表与触发器
    root_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp_parent = os.path.join(root_repo, ".test_tmp")
    os.makedirs(tmp_parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix="fb_maint145_work_", dir=tmp_parent)
    srv = None
    hk_ctrl = None
    try:
        # ---------- 1. Win32 全局热键解析与注册 ----------
        check("hotkey parse Alt+Space", hotkey.parse_combo("Alt+Space") == (0x0001, 0x20))
        check("hotkey parse <alt>+<space>", hotkey.parse_combo("<alt>+<space>") == (0x0001, 0x20))
        check("hotkey parse Ctrl+Shift+F", hotkey.parse_combo("Ctrl+Shift+F") == (0x0002 | 0x0004, ord("F")))
        check("hotkey parse Win+K", hotkey.parse_combo("Win+K") == (0x0008, ord("K")))
        check("hotkey reject bare key", hotkey.parse_combo("Space") is None)
        check("hotkey reject unknown key", hotkey.parse_combo("Alt+NoSuchKey") is None)

        hk_ctrl = hotkey.HotkeyController(lambda: None)
        r_hk = hk_ctrl.apply(True, "Ctrl+Alt+Shift+F11")
        check("hotkey Win32 register/apply", r_hk.get("ok") is True, str(r_hk))
        r_off = hk_ctrl.apply(False, "Ctrl+Alt+Shift+F11")
        check("hotkey Win32 unregister", r_off.get("ok") is True, str(r_off))
        hk_ctrl.stop()

        ap = api_mod.Api()
        check("Api.set_hotkey rejects invalid combo",
              ap.set_hotkey(True, "Space")["ok"] is False)

        # ---------- 2. 自定义分类规则增删改与去重覆盖 ----------
        db.add_rule(".dat", "数据", "实验")
        all_r = {r["pattern"]: r for r in rules.all_rules()["rules"]}
        check("新扩展名出现在 all_rules",
              "dat" in all_r and all_r["dat"]["custom"] is True and all_r["dat"]["sub"] == "实验",
              str(all_r.get("dat")))

        db.add_rule("pdf", "其他", "归档PDF")
        db.add_rule("pdf", "文档", "合同PDF")  # 同 pattern 二次写入走 UPSERT 不重复
        all_r2 = {r["pattern"]: r for r in rules.all_rules()["rules"]}
        conn = db.get_conn()
        try:
            pdf_cnt = conn.execute("SELECT COUNT(*) c FROM rules WHERE pattern='pdf'").fetchone()["c"]
        finally:
            conn.close()
        check("内置扩展名覆盖生效且库内唯一",
              pdf_cnt == 1 and all_r2["pdf"]["sub"] == "合同PDF" and all_r2["pdf"]["custom"] is True,
              f"cnt={pdf_cnt}, row={all_r2.get('pdf')}")
        ap.delete_rule("pdf")
        all_r3 = {r["pattern"]: r for r in rules.all_rules()["rules"]}
        check("删除覆盖后恢复内置规则",
              all_r3["pdf"]["category"] == "文档" and all_r3["pdf"]["custom"] is False)

        # ---------- 3. 目录前缀隔离与带空格 path:"..." 查询 ----------
        dir_a = os.path.join(work, "proj")
        dir_b = os.path.join(work, "proj_extra")
        dir_sp = os.path.join(work, "folder with space")
        for d in (dir_a, dir_b, dir_sp):
            os.makedirs(d, exist_ok=True)
        fa = os.path.join(dir_a, "a.txt")
        fb = os.path.join(dir_b, "b.txt")
        fsp = os.path.join(dir_sp, "note.txt")
        for fp, content in ((fa, "alpha"), (fb, "bravo_longer"), (fsp, "space_hit")):
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
            fileindex.upsert_file(fp)

        sp_a = ap._space_analysis_compute(root=dir_a)
        check("space_analysis root 不误吞同前缀兄弟目录",
              sp_a["total"] == os.path.getsize(fa), f"{sp_a['total']} vs {os.path.getsize(fa)}")

        res_sp = fileindex.search(query=f'path:"{dir_sp}"')
        check('path:"带空格目录" 精确命中',
              len(res_sp["items"]) == 1 and res_sp["items"][0]["path"] == os.path.abspath(fsp),
              str(res_sp))

        fileindex.remove_under(dir_a)
        rem_b = fileindex.search(query=f'path:"{dir_b}"')
        check("remove_under 不误删同前缀兄弟目录",
              len(rem_b["items"]) == 1 and rem_b["items"][0]["path"] == os.path.abspath(fb),
              str(rem_b))

        # ---------- 4. Watcher 目录整体改名同步 ----------
        mv_src = os.path.join(work, "mv_old")
        mv_dst = os.path.join(work, "mv_new")
        os.makedirs(mv_src, exist_ok=True)
        mv_f = os.path.join(mv_src, "doc.txt")
        with open(mv_f, "w", encoding="utf-8") as f:
            f.write("hello")
        fileindex.upsert_file(mv_f)
        db.upsert_img(mv_f, 1.0, 5, "old img", None, ocr="ocr")
        os.rename(mv_src, mv_dst)
        we = watcher.WatchEngine()
        we.move_dir(mv_src, mv_dst)
        we._drain_queue()
        check("move_dir 清理旧路径索引并入库新路径",
              len(fileindex.search(query=f'path:"{mv_src}"')["items"]) == 0
              and len(fileindex.search(query=f'path:"{mv_dst}"')["items"]) == 1
              and db.img_rows_by_paths([mv_f]) == {})

        # ---------- 5. Planner 同名冲突防覆盖 & 遵守 images_by_year ----------
        s1 = os.path.join(work, "s1", "same.txt")
        s2 = os.path.join(work, "s2", "same.txt")
        os.makedirs(os.path.dirname(s1), exist_ok=True)
        os.makedirs(os.path.dirname(s2), exist_ok=True)
        for p in (s1, s2):
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
        tgt = os.path.join(work, "organized")
        plan = planner.build_plan([
            {"path": s1, "name": "same.txt", "ext": "txt", "size": 1, "category": "文档"},
            {"path": s2, "name": "same.txt", "ext": "txt", "size": 1, "category": "文档"},
        ], tgt)
        dsts = [r["dst"] for r in plan]
        check("Planner 跨子目录同名文件预留不撞车",
              len(set(dsts)) == 2 and any("(1)" in d for d in dsts), str(dsts))

        img_p = os.path.join(work, "s1", "pic.png")
        from PIL import Image
        Image.new("RGB", (32, 32), (120, 80, 40)).save(img_p)
        db.set_setting("images_by_year", "0")
        plan_img = planner.build_plan([
            {"path": img_p, "name": "pic.png", "ext": "png", "size": 100, "category": "图片"},
        ], tgt)
        check("Planner 默认读取设置中的 images_by_year=0",
              os.path.dirname(plan_img[0]["dst"]) == os.path.join(tgt, "图片"),
              plan_img[0]["dst"])
        db.set_setting("images_by_year", "1")

        # ---------- 6. Windows 仅大小写重命名 + 撤销/重做 + 纯 copy 不标已撤销 ----------
        case_dir = os.path.join(work, "case_test")
        os.makedirs(case_dir, exist_ok=True)
        case_src = os.path.join(case_dir, "readme.md")
        with open(case_src, "w", encoding="utf-8") as f:
            f.write("# title")
        fileindex.upsert_file(case_src)
        ren = ap.rename_files([{"src": case_src, "new_name": "README.md"}])
        check("仅大小写改名不追加 (1)",
              ren["results"][0]["ok"]
              and os.path.basename(ren["results"][0]["dst"]) == "README.md"
              and os.listdir(case_dir) == ["README.md"],
              str(ren))
        u_res = executor.undo(ren["batch_id"])
        check("仅大小写改名可原样撤销",
              u_res["undone"] == 1 and os.listdir(case_dir) == ["readme.md"], str(u_res))
        r_res = executor.redo(ren["batch_id"])
        check("仅大小写改名可原样重做",
              r_res["redone"] == 1 and os.listdir(case_dir) == ["README.md"], str(r_res))

        copy_bid = db.new_batch_id()
        db.log_operation(copy_bid, "copy", case_src, os.path.join(case_dir, "copy.md"))
        u_copy = executor.undo(copy_bid)
        b_map = {b["batch_id"]: b for b in db.list_batches()}
        check("纯 copy 批次撤销后不伪装成已撤销",
              u_copy["undone"] == 0 and b_map[copy_bid]["undone_n"] == 0, str(b_map.get(copy_bid)))

        # ---------- 7. 待清理隔离区列表与安全清理 ----------
        dup_dir = os.path.join(work, "dups")
        os.makedirs(dup_dir, exist_ok=True)
        d1 = os.path.join(dup_dir, "dup1.txt")
        d2 = os.path.join(dup_dir, "dup2.txt")
        for p in (d1, d2):
            with open(p, "w", encoding="utf-8") as f:
                f.write("identical_payload_12345")
            fileindex.upsert_file(p)
        cl = cleanup.dedupe_cleanup([{"hash": "h1", "paths": [d1, d2]}], keep_paths=[d1])
        check("dedupe_cleanup 移入待清理并同步移除索引",
              cl["moved"] == 1
              and len(fileindex.search(query=f'path:"{dup_dir}"')["items"]) == 1,
              str(cl))
        qst = ap.quarantine_status()
        check("quarantine_status 统计批次与字节数",
              qst["total_files"] == 1 and qst["total_size"] > 0 and len(qst["items"]) == 1,
              str(qst))

        from backend import trash as trash_mod
        orig_trash = trash_mod.send_to_recycle_bin
        trash_mod.send_to_recycle_bin = lambda p: shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        try:
            bad_purge = ap.quarantine_purge(["sub/illegal"])
            check("quarantine_purge 拒绝非直接子项名", bad_purge["purged"] == 0 and bad_purge["failed"] == 1)
            ok_purge = ap.quarantine_purge(None)
            check("quarantine_purge 清空隔离区并统计释放空间",
                  ok_purge["ok"] and ok_purge["purged"] == 1 and ok_purge["freed_bytes"] > 0,
                  str(ok_purge))
            check("quarantine_status 清空后为 0", ap.quarantine_status()["total_files"] == 0)
        finally:
            trash_mod.send_to_recycle_bin = orig_trash

        # ---------- 8. 压缩包只读预览、TIFF 转码与音视频 HTTP Range ----------
        root_res = fileindex.add_root(work)
        check("注册测试监控根", root_res.get("ok") is True, str(root_res))
        srv = thumbserver.ThumbServer()
        port = srv.start()
        ap.thumb.port = port

        zp = os.path.join(work, "bundle.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("<script>evil</script>.txt", "hello archive")
        zprev = ap.preview_file(zp)
        check("ZIP 只读目录预览且转义文件名",
              zprev.get("type") == "html"
              and "&lt;script&gt;evil&lt;/script&gt;.txt" in zprev.get("content", "")
              and "<script>" not in zprev.get("content", ""),
              str(zprev)[:120])

        tp = os.path.join(work, "bundle.tar.gz")
        with tarfile.open(tp, "w:gz") as tf:
            raw = b"tar content"
            ti = tarfile.TarInfo("folder/readme.txt")
            ti.size = len(raw)
            tf.addfile(ti, io.BytesIO(raw))
        tprev = ap.preview_file(tp)
        check("TAR.GZ 只读目录预览",
              tprev.get("type") == "html" and "folder/readme.txt" in tprev.get("content", ""))

        mp3 = os.path.join(work, "clip.mp3")
        with open(mp3, "wb") as f:
            f.write(b"0123456789ABCDEF")
        aprev = ap.preview_file(mp3)
        check("音频文件返回 audio 预览类型", aprev.get("type") == "audio", str(aprev))
        req = urllib.request.Request(aprev["url"], headers={"Range": "bytes=4-9"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            check("ThumbServer 支持 HTTP 206 Range 分段读取",
                  resp.status == 206 and body == b"456789"
                  and resp.headers.get("Content-Range") == "bytes 4-9/16",
                  f"status={resp.status}, body={body}")

        tif_p = os.path.join(work, "scan.tiff")
        Image.new("RGB", (40, 30), (30, 140, 200)).save(tif_p, format="TIFF")
        timg_prev = ap.preview_file(tif_p)
        with urllib.request.urlopen(timg_prev["url"], timeout=5) as resp:
            tbytes = resp.read()
            check("TIFF 预览自动转码为 JPEG",
                  resp.status == 200 and tbytes[:2] == b"\xff\xd8")

        # ---------- 9. 分块页码保留、RAG 列表意图、多维向量、LSH、混搭图搜、周报转义 ----------
        chunks = chunker.chunk_text("[第5页]\n# 违约责任\n" + "违约方应承担赔偿责任。" * 15, title="合同.pdf")
        check("chunker 同时保留 PDF 页码与章节标题",
              bool(chunks) and chunks[0].splitlines()[0] == "合同.pdf · [第5页] 违约责任",
              str(chunks[0][:60] if chunks else None))

        xl_chunks = chunker.chunk_text("[工作表 收入]\n100\n\n[工作表 支出]\n200", title="财报.xlsx")
        check("chunker 按 Excel 工作表硬切分",
              len(xl_chunks) == 2 and xl_chunks[0].splitlines()[0] == "财报.xlsx · [工作表 收入]",
              str(xl_chunks))

        check("rag 列表意图不误伤普通业务提问",
              rag._is_list_intent("采购清单有哪些审批节点？") is False
              and rag._is_list_intent("报销材料列表是什么") is False
              and rag._is_list_intent("知识库里有哪些文件") is True)

        import numpy as np
        conn = db.get_conn()
        try:
            conn.execute("INSERT INTO kb_folders(id, path, added_at, source) VALUES(91, 'D:/m', 0, 'manual')")
            conn.execute("INSERT INTO kb_files(id, folder_id, path, mtime, size, status) "
                         "VALUES(91, 91, 'D:/m/a.txt', 0, 10, 'ok')")
            v3 = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
            v4 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32).tobytes()
            c3 = conn.execute("INSERT INTO chunks(file_id, seq, text, vec) VALUES(91, 0, 'dim3', ?)", (v3,)).lastrowid
            c4 = conn.execute("INSERT INTO chunks(file_id, seq, text, vec) VALUES(91, 1, 'dim4', ?)", (v4,)).lastrowid
            conn.commit()
        finally:
            conn.close()
        db.vec_index.invalidate()
        h3 = db.vec_index.search([1.0, 0.0, 0.0], k=3)
        h4 = db.vec_index.search([0.0, 1.0, 0.0, 0.0], k=3)
        check("VectorIndex 混合不同维度向量不崩溃且按同维度命中",
              [i for i, _ in h3] == [c3] and [i for i, _ in h4] == [c4],
              f"h3={h3}, h4={h4}")

        covered_mask = 0
        covered_bits = 0
        for shift, mask in simimg._LSH_SLICES:
            covered_mask |= (mask << shift)
            covered_bits += mask.bit_count()
        check("simimg 11 片不相交位切片完整覆盖 64 bit（d<=10 零漏检）",
              len(simimg._LSH_SLICES) == 11 and covered_bits == 64 and covered_mask == (1 << 64) - 1)

        # 混搭模式下 imgsearch 必须走 embed_client 而非 chat_client
        sample_img = os.path.join(work, "hybrid_test.png")
        Image.new("RGB", (32, 32), (10, 20, 30)).save(sample_img)
        called = {"chat_embed": 0, "embed_embed": 0}
        orig_vl_ready = imgsearch.vl_ready
        orig_get_chat = llm.get_chat_client
        orig_get_embed = llm.get_embed_client
        orig_emb_avail = llm.embed_available

        class _StubChat:
            def has_model(self, m):
                return True

            def describe_image(self, p, m):
                return ("海边日落", "")

            def embed_one(self, t):
                called["chat_embed"] += 1
                raise RuntimeError("chat client has no embedding endpoint")

        class _StubEmbed:
            def embed_one(self, t):
                called["embed_embed"] += 1
                return [0.5, 0.5, 0.0]

        try:
            imgsearch.vl_ready = lambda: True
            llm.get_chat_client = lambda: _StubChat()
            llm.get_embed_client = lambda: _StubEmbed()
            llm.embed_available = lambda: True
            res_img = imgsearch.describe_files([sample_img])
            check("imgsearch 混搭模式使用 get_embed_client 生成向量",
                  res_img["described"] == 1 and called["embed_embed"] == 1 and called["chat_embed"] == 0,
                  f"res={res_img}, called={called}")
        finally:
            imgsearch.vl_ready = orig_vl_ready
            llm.get_chat_client = orig_get_chat
            llm.get_embed_client = orig_get_embed
            llm.embed_available = orig_emb_avail

        rec = model_rec.recommend_local(ram_gb=8, vram_gb=12)
        check("model_rec 独显推荐同时校验系统内存下限",
              rec["tier"] != "gpu8", str(rec))

        html_out = report.export_html({
            "new_count": 1, "new_size": 10, "total_files": 1, "total_size": 10,
            "by_category": {"<b>文档</b>": {"n": 1, "size": 10}},
            "new_sample": [{"name": "<img src=x onerror=1>.txt", "category": "<b>文档</b>", "size": 10}],
            "big_files": [],
        }, week_start="2026-11-16<script>")
        check("report.export_html 转义全部文件名与类别",
              "<img" not in html_out and "<script>" not in html_out and "&lt;img" in html_out)

        # ---------- 10. 中英语言包键结构 100% 对称 ----------
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        zh_keys = _extract_js_keys(open(os.path.join(root_dir, "frontend", "src", "locales", "zh-CN.js"), encoding="utf-8").read())
        en_keys = _extract_js_keys(open(os.path.join(root_dir, "frontend", "src", "locales", "en.js"), encoding="utf-8").read())
        only_zh = sorted(zh_keys - en_keys)
        only_en = sorted(en_keys - zh_keys)
        check("zh-CN.js 与 en.js 键集合完全对称",
              not only_zh and not only_en, f"only_zh={only_zh}, only_en={only_en}")

    finally:
        if hk_ctrl:
            hk_ctrl.stop()
        if srv:
            srv.stop()
        db.close_all_conns()
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(_SANDBOX, ignore_errors=True)

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
