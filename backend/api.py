"""pywebview JS 桥接 API：前端 window.pywebview.api.<method>() 调用的全部方法。

长时间任务（索引/拉模型/问答）通过 window.__fbEvent(name, payload) 向前端推送进度。
"""
import json
import os
import subprocess
import sys
import threading
import time

import webview

from backend import db, fileindex
from backend.core import classifier, cleanup, dedupe, executor, planner, rules as rules_mod, scanner
from backend.knowledge import indexer, imgsearch, rag
from backend.ollama_client import OllamaClient, OllamaNotRunning
from backend import thumbserver, watcher


class ThumbServerHolder:
    def __init__(self):
        self.server = None
        self.port = None

    def start(self):
        self.server = thumbserver.ThumbServer()
        self.port = self.server.start()


class Api:
    def __init__(self):
        self._window = None
        db.init_db()
        self.watch_engine = None
        self.thumb = ThumbServerHolder()
        # dir_tree 5s 缓存：[last_compute_ts, valid, result]
        self._dir_tree_cache = [0.0, False, {"tree": []}]
        self._space_cache = {}   # root -> (ts, result)（space_analysis 60s 缓存）
        self._visible = True     # 窗口可见性（托盘隐藏时为 False）

    def set_window(self, window):
        self._window = window

    def set_visible(self, visible):
        """窗口显示/隐藏切换（main.py 托盘最小化时调用）。
        隐藏期间 _emit 不再向 WebView 推 JS：5 块盘的后台文件变动会持续唤醒
        隐藏的渲染器（CPU 占用 + pywebview evaluate_js 已知泄漏向量，
        r0x0r/pywebview#137）。重新显示时发 lib_resync 让前端自愈。"""
        was = getattr(self, "_visible", True)
        self._visible = visible
        if visible and not was:
            self._emit("lib_resync", {})

    # ---------- 后台服务启动（main.py 调用） ----------

    def start_services(self):
        """全量索引 + 实时监控 + 缩略图服务 + 自动知识库。幂等。"""
        fileindex.ensure_default_roots()
        # 启动时清理孤儿 KB（用户从监控根删了根，但 KB auto 引用没同步）
        n = fileindex.cleanup_orphan_kb_folders()
        if n:
            print(f"[start] cleaned {n} orphan KB folders")
        if self.thumb.server is None:
            self.thumb.start()
        if self.watch_engine is None:
            self.watch_engine = watcher.WatchEngine(
                on_event=lambda kind, path, cat: self._emit("lib_file_event",
                                                            {"kind": kind, "path": path,
                                                             "category": cat}),
                on_docs=self._auto_kb_hook,
                on_imgs=self._auto_img_hook,
                on_removed=self._auto_removed_hook,
                on_organize=self._auto_organize_hook,
            )
            self.watch_engine.start()

        # Ollama 状态后台预热：本机回环被安全软件拦截时探测要 2s，
        # 提前探好等前端 get_status 时 30s 缓存已热，首屏不等
        threading.Thread(target=self._warm_status, daemon=True,
                         name="fb-warm-status").start()

        def after_scan():
            # 文件总索引就绪后：再次清理孤儿 KB（rescan 可能删除过时的 auto 根）
            try:
                fileindex.cleanup_orphan_kb_folders()
            except Exception:
                pass
            # 文件总索引就绪后：同步 auto 知识库文件夹并做首轮索引
            try:
                indexer.sync_auto_folders()
                if self._auto_kb_enabled():
                    self.index_all_auto_folders()
                if self._img_semantic_enabled() and imgsearch.vl_ready():
                    self.describe_backlog()
            except Exception as e:
                print("after_scan error:", e)
            # 预热重缓存：dir_tree / space_analysis 各要全表聚合 2-5s（机械盘），
            # 扫描完后台算好，用户首次打开页面即秒回
            threading.Thread(target=self._warm_caches, daemon=True,
                             name="fb-warm-caches").start()

        # 24 小时内扫过就不再全盘重扫（5 块盘 83 万文件 walk 一遍代价太高；
        # 运行期变动由 watcher 实时维护，兜底重扫见 main.py 的每周检查）
        try:
            last_scan = float(db.get_setting("last_full_scan_at", "0") or "0")
        except (TypeError, ValueError):
            last_scan = 0.0
        if _t.time() - last_scan < 24 * 3600:
            print("[start] skip full rescan (scanned <24h ago)")
            threading.Thread(target=after_scan, daemon=True).start()
        else:
            self.rescan_library(after=after_scan)

    def _warm_status(self):
        """后台预热 Ollama 状态缓存（30s TTL 的第一次探测）。"""
        try:
            OllamaClient().status()
        except Exception:
            pass

    def _warm_caches(self):
        """后台预热 dir_tree / space_analysis 聚合缓存。"""
        self._lower_thread_priority()
        try:
            self.dir_tree()
        except Exception:
            pass
        try:
            self.space_analysis()
        except Exception:
            pass

    # ---------- 自动知识库 / 图片语义钩子 ----------

    def _auto_kb_enabled(self):
        from backend import db as _db
        return _db.get_setting("auto_kb", "0") == "1"  # 默认关：自动 KB 会扫描全文 + 调 embedding 极易撑爆内存

    def _img_semantic_enabled(self):
        from backend import db as _db
        return _db.get_setting("img_semantic", "0") == "1"  # 默认关

    def _auto_kb_hook(self, paths):
        if not self._auto_kb_enabled():
            return
        # 关键：watcher 把所有新文件（包括总索引里非 KB 的文件）都 emit added，
        # 自动 KB 钩子必须过滤：只对在 KB auto 文件夹内的文件做 AI（避免全盘爆炸）
        from backend import db as _db
        from backend.knowledge import indexer as _idx
        kb_paths = [p for p in paths if _db.file_in_any_kb_folder(p)]
        if not kb_paths:
            return
        try:
            result = _idx.index_files(kb_paths)
            if result.get("indexed"):
                self._emit("auto_kb_done", result)
        except Exception as e:
            print("auto_kb error:", e)

    def _auto_img_hook(self, paths):
        if not self._img_semantic_enabled():
            return
        try:
            imgsearch.describe_files(paths)
        except Exception as e:
            print("auto_img error:", e)

    def _auto_removed_hook(self, path):
        try:
            indexer.remove_kb_file(path)
            db.remove_img(path)
            db.img_vec_index.invalidate()
        except Exception:
            pass

    def _auto_organize_hook(self, path, category):
        """新文件出现 → 自动整理（仅当设置开启；report/move 两种模式）。"""
        try:
            from backend.core import autoorganize
            r = autoorganize.maybe_organize(path)
            if r:
                self._emit("auto_organize", r)
        except Exception:
            pass

    def index_all_auto_folders(self):
        """首轮索引全部 auto 知识库文件夹（后台）。"""
        def worker():
            try:
                for f in indexer.list_folders():
                    if f.get("source") != "auto":
                        continue
                    self._emit("kb_index_start", {"folder_id": f["id"], "auto": True})

                    def cb(stage, i, n, detail, fid=f["id"]):
                        self._emit("kb_index_progress",
                                   {"folder_id": fid, "stage": stage, "i": i, "n": n,
                                    "detail": detail, "auto": True})

                    result = indexer.index_folder(f["id"], cb)
                    self._emit("kb_index_done", {"folder_id": f["id"], "result": result,
                                                 "auto": True})
            except Exception as e:
                self._emit("kb_index_error", str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def describe_backlog(self):
        """为全部未描述图片生成语义描述（后台，进度走事件）。"""

        def worker():
            try:
                paths = imgsearch.pending_images()
                if not paths:
                    self._emit("img_desc_done", {"described": 0, "pending": 0})
                    return
                self._emit("img_desc_start", {"pending": len(paths)})

                def cb(i, n, name):
                    self._emit("img_desc_progress", {"i": i, "n": n, "name": name})

                result = imgsearch.describe_files(paths, cb)
                self._emit("img_desc_done", result)
            except Exception as e:
                self._emit("img_desc_error", str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def rescan_library(self, after=None):
        """后台全量扫描（启动时与手动刷新时调用）。after: 扫描完成后的回调。"""

        def worker():
            self._lower_thread_priority()  # 扫描 65 万+文件不应抢占前台 CPU
            try:
                self._emit("lib_scan_start", {})

                def cb(stage, i, n, detail):
                    self._emit("lib_scan_progress", {"stage": stage, "i": i, "n": n,
                                                     "detail": detail})

                result = fileindex.full_scan(cb)
                db.set_setting("last_full_scan_at", str(_t.time()))
                self._emit("lib_scan_done", {"result": result,
                                             "stats": fileindex.index_stats()})
                if after:
                    after()
            except Exception as e:
                self._emit("lib_scan_error", str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    # ---------- 事件推送 ----------

    @staticmethod
    def _lower_thread_priority():
        """把当前线程降到 BELOW_NORMAL：后台扫描/索引不与前台 UI 抢 CPU。"""
        try:
            import ctypes
            THREAD_PRIORITY_BELOW_NORMAL = -1
            handle = ctypes.c_void_p(-2)  # GetCurrentThread() 伪句柄
            ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_BELOW_NORMAL)
        except Exception:
            pass

    # 隐藏期间仍允许推送的事件：问答流式 token 丢了无法补发（答案会截断）
    _EMIT_ALWAYS = {"ask_delta", "ask_done"}

    def _emit(self, name, payload):
        if self._window is None:
            return
        if not getattr(self, "_visible", True) and name not in self._EMIT_ALWAYS:
            return  # 托盘隐藏期间不唤醒渲染器；显示时用 lib_resync 补偿
        try:
            data = json.dumps({"name": name, "payload": payload}, ensure_ascii=True)
            self._window.evaluate_js(f"window.__fbEvent && window.__fbEvent({data})")
        except Exception:
            pass

    # ---------- 通用 ----------

    # ---------- LLM 接入方式（本地 Ollama / 云端 API） ----------

    def llm_provider_status(self):
        from backend import llm as llm_mod
        return {"summary": llm_mod.provider_summary(), "presets": llm_mod.API_PRESETS}

    def save_llm_provider(self, mode, api_base="", api_key="", chat_model="",
                          embed_source="follow", embed_model=""):
        """保存接入配置。mode: ollama|api；embed_source: follow|ollama|api。"""
        from backend import llm as llm_mod
        if mode not in ("ollama", "api"):
            return {"ok": False, "error": "无效模式"}
        if mode == "api" and not (api_base.strip() and api_key.strip() and chat_model.strip()):
            return {"ok": False, "error": "API 模式需要填写服务地址、密钥和对话模型名"}
        if embed_source == "api" and not embed_model.strip():
            return {"ok": False, "error": "云端向量化需要填写向量模型名（或改用本地 bge-m3）"}
        db.set_setting("llm_mode", mode)
        db.set_setting("embed_source", embed_source)
        db.set_setting("api_base", api_base.strip().rstrip("/"))
        if api_key.strip():
            db.set_setting("api_key", api_key.strip())  # 不填则保留原密钥
        db.set_setting("api_chat_model", chat_model.strip())
        db.set_setting("api_embed_model", embed_model.strip())
        return {"ok": True, "summary": llm_mod.provider_summary()}

    def test_llm_provider(self):
        from backend import llm as llm_mod
        return llm_mod.test_chat()

    def get_status(self):
        """全局状态：模型接入、知识库统计、文件总索引。"""
        from backend import llm as llm_mod
        client = OllamaClient()
        try:
            lib = fileindex.index_stats()
            lib["categories"] = fileindex.category_stats()
        except Exception:
            lib = {"files": 0, "total_size": 0, "categories": []}
        # 内置能力（不依赖 Ollama）：Windows 系统 OCR（图片文字/扫描件 PDF）
        try:
            from backend.knowledge import winocr as _winocr
            winocr_status = {"available": _winocr.available(),
                             "languages": _winocr.languages()[:4]}
        except Exception:
            winocr_status = {"available": False, "languages": []}
        return {
            "ollama": client.status(),
            "chat_model": client.chat_model(),
            "embed_model": client.embed_model(),
            "kb": db.stats(),
            "winocr": winocr_status,
            "images_by_year": db.get_setting("images_by_year", "1"),
            "auto_kb": db.get_setting("auto_kb", "1") == "1",
            "img_semantic": db.get_setting("img_semantic", "0") == "1",
            "library": lib,
            "thumb_base": f"http://127.0.0.1:{self.thumb.port}" if self.thumb.port else None,
            "watching": len(fileindex.enabled_root_paths()),
            "llm": llm_mod.provider_summary(),
        }

    def pick_folder(self):
        """系统文件夹选择对话框。"""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return str(result[0])
        return None

    def open_path(self, path):
        """用系统默认程序打开文件/文件夹（双击行为）。"""
        if path:
            os.startfile(os.path.normpath(path))

    def open_location(self, path):
        """在资源管理器中打开并选中文件（或打开文件夹）。"""
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif os.path.isdir(path):
            os.startfile(path)

    def summarize_file(self, path, max_chars=8000):
        """AI 总结文档（pdf/docx/pptx/xlsx/txt 等）：提取文本 → 本地/云端模型总结。
        扫描版 PDF（无文本层）自动 fallback 到图片语义 OCR（描述 + 总结）。"""
        from backend.knowledge.parsers import parse_file, SUPPORTED
        from backend import llm as llm_mod
        import backend.knowledge.imgsearch as _imgsearch

        ext = os.path.splitext(path or "")[1].lstrip(".").lower()
        if ext not in SUPPORTED:
            return {"ok": False, "error": f"暂不支持总结 {ext or '未知'} 格式文件"}
        try:
            text = parse_file(path) or ""
        except Exception as e:
            return {"ok": False, "error": f"读取文件失败：{str(e)[:120]}"}

        # 扫描版 PDF（无文本层）→ 直接调底层 describe_image 拿描述字符串
        # imgsearch.describe_files 返回 dict（计数），不返回 descr 文本
        if not text.strip():
            if ext in ("pdf", "png", "jpg", "jpeg", "bmp", "webp") and _imgsearch.vl_ready():
                try:
                    chat = llm_mod.get_chat_client()
                    descr, ocr = chat.describe_image(path, _imgsearch.vl_model())
                    if (descr or "").strip():
                        return {"ok": True,
                                "summary": (f"【图片描述】{descr}\n【图中文字】{ocr}" if ocr else descr).strip(),
                                "source": "image_ocr",
                                "note": "扫描版文件，由图片语义 OCR 描述生成"}
                except Exception as e:
                    return {"ok": False, "error": f"图片语义 OCR 失败：{e}；可安装 VL 模型或换文本版 PDF"}
            return {"ok": False, "error": "未能从文件中提取到文本（可能为扫描版 PDF，可用图片语义 OCR）"}

        text = text[:max_chars]
        if not llm_mod.chat_available():
            return {"ok": False, "error": "对话模型不可用：" + llm_mod.chat_status()["reason"]}
        try:
            client = llm_mod.get_chat_client()
            reply = client.chat([
                {"role": "system",
                 "content": "你是 FileButler 的文件摘要助手。请用简洁中文总结文档要点，输出 3-5 条编号列表。"
                            "严格禁止使用任何 markdown 符号（不要用 **加粗**、# 标题、- 列表符号、星号、下划线等），"
                            "只用纯文本，编号用「1. 2. 3.」即可，句子之间用换行分隔。"},
                {"role": "user", "content": f"文档内容（前 {max_chars} 字）：\n{text}"},
            ], options={"temperature": 0.2, "num_predict": 600})
            if not (reply or "").strip():
                return {"ok": False, "error": "模型未返回总结内容"}
            return {"ok": True, "summary": reply.strip(), "source_len": len(text)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    # ---------- 整理引擎 ----------

    def scan_folder(self, path):
        """扫描目录，返回文件列表（含规则分类结果）。"""
        rules = rules_mod.load_user_rules()
        files = scanner.scan_folder(path)
        for fi in files:
            hit = rules_mod.classify_by_ext(fi["ext"], rules)
            if hit:
                fi["category"], fi["sub"] = hit
                fi["rule_hit"] = True
            else:
                fi["category"], fi["sub"] = "其他", ""
                fi["rule_hit"] = False
        return {"root": path, "files": files,
                "ambiguous": sum(1 for f in files if not f["rule_hit"])}

    def ai_classify(self, files):
        """对模糊文件做 AI 分类（后台线程执行，进度走事件）。"""
        def worker():
            try:
                from backend import llm as llm_mod
                if not llm_mod.chat_available():
                    self._emit("ai_classify_error",
                               "对话模型不可用：" + llm_mod.chat_status()["reason"])
                    return
                client = llm_mod.get_chat_client()
                self._emit("ai_classify_start", {"total": len(files)})

                def cb(i, n, name):
                    self._emit("ai_classify_progress", {"i": i, "n": n, "name": name})

                result = classifier.ai_classify(files, client, cb)
                for fi in files:
                    r = result.get(fi["path"])
                    if r:
                        fi["ai_category"], fi["ai_reason"] = r[0], r[1]
                self._emit("ai_classify_done", {"files": files})
            except Exception as e:
                self._emit("ai_classify_error", str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def build_plan(self, files, target_root, category_map=None, images_by_year=True):
        """生成整理计划（预览数据）。"""
        plan = planner.build_plan(files, target_root, category_map, images_by_year)
        return {"plan": plan, "summary": planner.summarize(plan)}

    def execute_plan(self, rows):
        """执行勾选的计划行。"""
        return executor.execute(rows)

    def undo_batch(self, batch_id):
        return executor.undo(batch_id)

    def redo_batch(self, batch_id):
        """取消撤销：把已撤销批次的文件重新移回整理后的位置。"""
        return executor.redo(batch_id)

    def history(self):
        return {"batches": db.list_batches()}

    def batch_operations(self, batch_id):
        return {"ops": db.list_operations(batch_id)}

    def find_duplicates(self, files):
        """重复检测（同步执行，文件量一般不大）。"""
        groups = dedupe.find_duplicates(files)
        total_waste = sum(g["size"] * (len(g["paths"]) - 1) for g in groups)
        return {"groups": groups, "total_waste": total_waste}

    def find_similar_images(self, files, threshold=10):
        """相似图片检测：dHash 感知哈希，找出缩放/压缩/连拍等近似重复。"""
        from backend.core import simimg
        groups = simimg.find_similar(files, threshold=threshold)
        total_waste = sum(g["size"] * (len(g["paths"]) - 1) for g in groups)
        return {"groups": groups, "total_waste": total_waste}

    # ---------- 批量文件操作 ----------

    def rename_files(self, rows):
        """批量重命名（改文件名，目录不变）。rows: [{src, new_name}]。
        冲突自动加 (1) 序号绝不覆盖；记入操作日志可撤销。"""
        batch_id = db.new_batch_id()
        results = []
        for r in rows:
            src = r["src"]
            new_name = (r.get("new_name") or "").strip()
            try:
                if not new_name or os.path.basename(src) == new_name:
                    continue
                dst = planner._unique_path(os.path.join(os.path.dirname(src), new_name))
                os.rename(src, dst)
                db.log_operation(batch_id, "rename", src, dst)
                try:
                    fileindex.remove_file(src)
                    fileindex.upsert_file(dst)
                except Exception:
                    pass  # 索引更新失败不影响文件操作，下轮扫描自愈
                results.append({"src": src, "dst": dst, "ok": True})
            except Exception as e:
                results.append({"src": src, "ok": False, "error": str(e)})
        return {"batch_id": batch_id, "results": results}

    def ai_rename(self, files):
        """本地模型批量建议新文件名（≤20 个）。files: [{path, name, ext, category}]。"""
        import json as _json
        from backend import llm as llm_mod
        files = list(files)[:20]
        lines = "\n".join(f"{i + 1}. {f['name']}" for i, f in enumerate(files))
        prompt = (
            "你是文件整理助手。为下列文件各起一个更清晰的中文文件名（保留原扩展名），"
            "按顺序只输出 JSON 数组，例如 [{\"i\":1,\"new_name\":\"旅行照片-1.jpg\"}]：\n"
            + lines
        )
        suggestions = []
        try:
            client = llm_mod.get_chat_client()
            answer = client.chat(
                [{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 400}, think=False)
            text = answer.strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            arr = _json.loads(text)
            for obj in arr:
                idx = int(obj.get("i", 0)) - 1
                if 0 <= idx < len(files):
                    suggestions.append({"src": files[idx]["path"],
                                        "new_name": str(obj.get("new_name", "")).strip()})
        except Exception:
            suggestions = []
        return {"suggestions": suggestions}

    def trash_files(self, paths):
        """把文件/文件夹移入系统回收站（可恢复），记入操作日志。"""
        from backend import trash
        batch_id = db.new_batch_id()
        results = []
        for p in paths:
            try:
                trash.send_to_recycle_bin(p)
                db.log_operation(batch_id, "trash", p, "回收站")
                try:
                    fileindex.remove_file(p)
                except Exception:
                    pass
                results.append({"path": p, "ok": True})
            except Exception as e:
                results.append({"path": p, "ok": False, "error": str(e)})
        return {"batch_id": batch_id, "results": results}

    def space_analysis(self, root=None):
        """按父目录与类别聚合文件占用空间（TOP 100 目录）。
        83 万行全表聚合在机械盘上约 2s 且 fetchall 有数百 MB 内存尖峰，
        改为：游标流式聚合（不攒行）+ 60s TTL 缓存，扫描完成后自动预热。"""
        import time as _t
        cache = self._space_cache
        now = _t.time()
        hit = cache.get(root)
        if hit and now - hit[0] < 60:
            return hit[1]
        dirs, cats, total = {}, {}, 0
        conn = db.get_conn()
        try:
            cond, params = "", []
            if root:
                cond = "WHERE path LIKE ?"
                params = [root.rstrip("/\\") + "%"]
            # 游标迭代代替 fetchall：不把 83 万行一次性搬进 Python 内存
            for r in conn.execute(
                    f"SELECT path, size, category FROM file_index {cond}", params):
                # 防御性：用户数据库里 size 列历史上可能存了 text 类型（脏数据）
                try:
                    size = int(r["size"] or 0)
                except (TypeError, ValueError):
                    size = 0
                total += size
                d = os.path.dirname(r["path"]) or r["path"]
                dirs[d] = dirs.get(d, 0) + size
                c = r["category"] or "其他"
                cats[c] = cats.get(c, 0) + size
        finally:
            conn.close()
        by_dir = [{"dir": d, "size": s} for d, s in
                  sorted(dirs.items(), key=lambda kv: -kv[1])[:100]]
        by_category = [{"category": c, "size": s} for c, s in
                       sorted(cats.items(), key=lambda kv: -kv[1])]
        result = {"by_dir": by_dir, "by_category": by_category, "total": total}
        cache[root] = (now, result)
        return result

    # ---------- 文件标签 ----------

    def list_tags(self):
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT t.id, t.name, COUNT(ft.path) AS c FROM tags t "
                "LEFT JOIN file_tags ft ON ft.tag_id = t.id "
                "GROUP BY t.id ORDER BY t.name").fetchall()
            return {"tags": [dict(r) for r in rows]}
        finally:
            conn.close()

    def add_tag(self, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "标签名不能为空"}
        conn = db.get_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            conn.commit()
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            return {"ok": True, "id": row["id"]}
        finally:
            conn.close()

    def delete_tag(self, tag_id):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM file_tags WHERE tag_id=?", (tag_id,))
            conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def list_file_tags(self, path):
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT t.id, t.name FROM file_tags ft JOIN tags t ON t.id=ft.tag_id "
                "WHERE ft.path=?", (path,)).fetchall()
            return {"tags": [dict(r) for r in rows]}
        finally:
            conn.close()

    def set_file_tags(self, path, tag_ids):
        path = os.path.abspath(path)
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM file_tags WHERE path=?", (path,))
            for tid in (tag_ids or []):
                # 防御性：tag_id 必须是整数；脏数据跳过不抛错
                try:
                    tid_int = int(tid) if tid is not None else None
                except (TypeError, ValueError):
                    continue
                if tid_int is None:
                    continue
                conn.execute("INSERT OR IGNORE INTO file_tags(path, tag_id) VALUES(?,?)",
                             (path, tid_int))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    # ---------- 自动整理（可选项） ----------

    def auto_organize_status(self):
        from backend.core import autoorganize
        return autoorganize.status()

    def set_auto_organize(self, enabled, mode="report", root=""):
        db.set_setting("auto_organize_enabled", "1" if enabled else "0")
        db.set_setting("auto_organize_mode", mode)
        db.set_setting("auto_organize_root", root or "")
        return {"ok": True}

    # ---------- 应用信息 ----------

    def app_version(self):
        from backend import __version__, APP_NAME
        return {"name": APP_NAME, "version": __version__}

    def check_update(self):
        """检查新版本：读 settings 里的更新源（GitHub Releases JSON），对比本地版本。"""
        from backend import __version__
        url = db.get_setting("update_check_url", "")
        if not url:
            return {"ok": False, "error": "未配置更新源（设置项 update_check_url）",
                    "current": __version__}
        try:
            import json as _json
            import urllib.request as _url
            req = _url.Request(url, headers={"User-Agent": "FileButler/" + __version__})
            with _url.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            latest = str(data.get("version", "") or "").strip()
            download = data.get("download", "") or data.get("url", "")
            outdated = False
            if latest:
                def _num(v):
                    return [int(x) for x in str(v).replace("v", "").split(".")[:3]]
                try:
                    outdated = _num(latest) > _num(__version__)
                except Exception:
                    outdated = latest != __version__
            return {"ok": True, "current": __version__, "latest": latest or None,
                    "outdated": outdated, "download": download or None,
                    "notes": data.get("notes", "") or ""}
        except Exception as e:
            return {"ok": False, "error": f"检查失败：{e}", "current": __version__}

    # ---------- 知识库 ----------

    def kb_folders(self):
        return {"folders": indexer.list_folders()}

    def kb_add_folder(self, path):
        row = indexer.add_folder(path)
        return {"ok": True, "folder": row}

    def kb_remove_folder(self, folder_id):
        indexer.remove_folder(folder_id)
        return {"ok": True}

    def kb_index(self, folder_id):
        """索引（后台线程，进度走事件 kb_index_*）。"""
        def worker():
            try:
                self._emit("kb_index_start", {"folder_id": folder_id})

                def cb(stage, i, n, detail):
                    self._emit("kb_index_progress",
                               {"folder_id": folder_id, "stage": stage, "i": i, "n": n,
                                "detail": detail})

                result = indexer.index_folder(folder_id, cb)
                self._emit("kb_index_done", {"folder_id": folder_id, "result": result})
            except OllamaNotRunning as e:
                self._emit("kb_index_error", str(e))
            except Exception as e:
                self._emit("kb_index_error", str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def search(self, query, k=8):
        return rag.search(query, k)

    def ask(self, query, history=None):
        """RAG 问答（同步执行；生成过程经 fb_event 流式推送）。"""
        def cb(tok):
            self._emit("ask_delta", {"token": tok})

        try:
            result = rag.ask(query, stream_cb=cb, history=history)
            self._emit("ask_done", result)
            return result
        except OllamaNotRunning as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"问答失败：{e}"}

    # ---------- Ollama 管理 ----------

    def save_ollama_settings(self, host, chat_model):
        if host:
            db.set_setting("ollama_host", host.rstrip("/"))
        if chat_model:
            db.set_setting("chat_model", chat_model)
        return {"ok": True}

    def recommend_model(self):
        """自动探测本机配置（内存/显存），推荐本地 Ollama 模型；同时返回云端各服务商推荐模型。"""
        from backend import model_rec
        cfg = model_rec.detect_config()
        local = model_rec.recommend_local(cfg["ram_gb"], cfg["vram_gb"])
        return {
            "local": local,
            "config": cfg,
            "cloud": model_rec.CLOUD_RECOMMEND,
            # 兼容旧字段
            "model": local["model"],
            "ram_gb": cfg["ram_gb"],
        }

    def pull_model(self, model):
        """拉取模型（后台线程，进度走事件）。"""
        def worker():
            client = OllamaClient()
            try:
                if not client.status()["running"]:
                    self._emit("pull_error", "Ollama 未运行，请先安装并启动 Ollama")
                    return
                self._emit("pull_start", {"model": model})

                def cb(done, total, status):
                    pct = round(done * 100 / total, 1) if total else 0
                    self._emit("pull_progress",
                               {"model": model, "done": done, "total": total,
                                "pct": pct, "status": status})

                client.pull(model, cb)
                self._emit("pull_done", {"model": model})
            except Exception as e:
                self._emit("pull_error", f"{model}: {e}")

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    # ---------- 规则与设置 ----------

    def get_rules(self):
        return rules_mod.all_rules()

    def add_rule(self, pattern, category, sub=""):
        db.add_rule(pattern, category, sub)
        return {"ok": True}

    def delete_rule(self, pattern):
        """删除用户自定义规则（按 pattern 匹配）。"""
        for r in db.get_rules():
            if r["pattern"] == pattern.lstrip(".").lower():
                db.delete_rule(r["id"])
        return {"ok": True}

    def set_images_by_year(self, enabled):
        db.set_setting("images_by_year", "1" if enabled else "0")
        return {"ok": True}

    # ---------- 文件总索引（Everything 式，只读） ----------

    def global_search(self, query):
        """全局搜索：文件名匹配（即时）+ 内容匹配（知识库 + 图片语义描述/OCR）。"""
        filename = fileindex.search(query=query, limit=200)
        content = rag.search(query, k=6) if query.strip() else {"mode": "vector", "results": [], "images": []}
        return {"query": query,
                "filename": filename,
                "content": {"mode": content.get("mode"),
                            "results": content.get("results", []),
                            "images": content.get("images", [])}}

    def browse_files(self, query=None, category=None, is_image=None, offset=0, limit=200,
                     sort_by="mtime", sort_order="desc", tag_ids=None):
        """分页浏览文件总索引。"""
        try:
            return fileindex.search(query=query or None, category=category or None,
                                    is_image=is_image or None, offset=offset, limit=limit,
                                    sort_by=sort_by, sort_order=sort_order, tag_ids=tag_ids)
        except Exception as e:
            # 临时状态（如 FTS 触发器异常）返回空结果，不弹错
            print(f"browse_files fallback: {e}")
            return {"items": [], "total": 0}

    def export_csv(self, query=None, category=None, sort_by="mtime", sort_order="desc"):
        """当前筛选结果导出 CSV（utf-8-sig BOM，Excel 直接打开不乱码）。"""
        import csv as _csv
        import io as _io
        try:
            r = fileindex.search(query=query or None, category=category or None,
                                 limit=100000, sort_by=sort_by, sort_order=sort_order)
        except Exception:
            r = {"items": []}
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["路径", "文件名", "扩展名", "类别", "大小(B)", "修改时间", "标签"])
        for it in r["items"]:
            ts = ""
            try:
                if it.get("mtime"):
                    ts = time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(it["mtime"]))
            except Exception:
                ts = ""
            w.writerow([it.get("path", ""), it.get("name", ""), it.get("ext", ""),
                        it.get("category", ""), it.get("size", 0), ts,
                        (it.get("tags") or "") or ""])
        return {"csv": "\ufeff" + buf.getvalue(), "count": len(r["items"])}

    def watch_roots(self):
        return {"roots": fileindex.list_roots(only_enabled=False)}

    # ---------- 目录树 + 搜索历史 ----------

    def dir_tree(self):
        """按父目录聚合 file_index 生成目录树（每目录含文件数与占用大小）。
        file_index 是绝对路径（带盘符如 C:\\Users\\...），第一层是盘符节点
        （'C:'、'D:'），盘符节点的 files/size 递归累加其下所有子目录。
        相对路径（无盘符前缀，如历史冒烟残留 'test/xx.txt'）跳过，不入树。
        30s 缓存：68 万文件聚合 2-5s（机械盘），缓存减少重复刷新。"""
        import re as _re
        import time as _t
        cache = self._dir_tree_cache
        now = _t.time()
        if cache[1] and now - cache[0] < 30:
            return cache[2]
        result = self._dir_tree_compute(_re)
        cache[0] = now
        cache[1] = True
        cache[2] = result
        return result

    def _dir_tree_compute(self, _re):
        import re as _re
        import time as _t
        import os as _os
        conn = db.get_conn()
        try:
            # 游标迭代代替 fetchall：不把 83 万行一次性搬进 Python 内存
            rows = conn.execute(
                "SELECT path, size FROM file_index WHERE path GLOB '[A-Za-z]:*'"
            )
            watch_roots = [r["path"] for r in conn.execute(
                "SELECT path FROM watch_roots WHERE enabled=1").fetchall()]
            dirs = {}
            for r in rows:
                path = r["path"]
                # rfind 最后一个 \ 之前的部分是 dirname（处理 X:\ 这种盘根没 \ 的情况）
                idx = path.rfind("\\")
                d = path[:idx] if idx > 2 else path  # X:\ 整盘，dirname = path
                e = dirs.setdefault(d, {"files": 0, "size": 0})
                e["files"] += 1
                try:
                    e["size"] += int(r["size"] or 0)
                except (TypeError, ValueError):
                    pass  # 脏数据：size 非数字时忽略体积
        finally:
            conn.close()
        # 把 enabled 监控根（如自动加的 D:\/E:\）也作为 0 文件节点入树，
        # 这样启动后立刻能看到所有盘根，不必等 rescan 完成
        for root in watch_roots:
            r = os.path.normpath(root).rstrip("\\/") + "\\"
            if r in dirs:
                continue
            # 只添加盘根级别（如 'C:\\' 'D:\\'），不递归整个树
            parts = [p for p in r.replace("\\", "/").split("/") if p]
            if len(parts) != 1:
                continue
            dirs[r] = {"files": 0, "size": 0}
        nodes = {}
        for d, e in dirs.items():
            parts = [p for p in d.replace("\\", "/").split("/") if p]
            cur = nodes
            for i, p in enumerate(parts):
                # 关键：cur 永远指向当前层级的"子节点容器"（节点的 _children），
                # 否则 setdefault 会把 p 错挂到当前节点本身而非 _children 里
                # 用 \\ 分隔 _dir 与 dirs key 格式一致
                cur = cur.setdefault(p, {"_dir": "\\".join(parts[:i + 1]),
                                         "_children": {}})["_children"]
        def _to_list(node):
            out = []
            for name, sub in node.items():
                if name == "_dir" or name == "_children":
                    continue
                full = sub["_dir"]
                children = _to_list(sub["_children"])
                # 优先用 dirs 精确数据；中间节点（dirs 无此 key）递归累加
                e = dirs.get(full)
                if e is None:
                    e = {"files": sum(c["files"] for c in children),
                         "size": sum(c["size"] for c in children)}
                out.append({"name": name, "path": full, "files": e["files"],
                            "size": e["size"], "children": children})
            out.sort(key=lambda x: -x["files"])
            return out
        # 只保留前 500 个目录，避免树过大
        tree = _to_list(nodes)
        return {"tree": tree}

    def save_search_history(self, q):
        """记录搜索历史（最近 20 条去重）。"""
        q = (q or "").strip()
        if not q or len(q) > 100:
            return {"ok": True}
        import json as _json
        try:
            hist = _json.loads(db.get_setting("search_history", "[]") or "[]")
        except Exception:
            hist = []
        if q in hist:
            hist.remove(q)
        hist.insert(0, q)
        db.set_setting("search_history", _json.dumps(hist[:20]))
        return {"ok": True}

    def search_history(self):
        """最近搜索词（新→旧）。"""
        import json as _json
        try:
            hist = _json.loads(db.get_setting("search_history", "[]") or "[]")
        except Exception:
            hist = []
        return {"history": hist}

    # ---------- 空文件夹清理 ----------

    def list_empty_dirs(self, root=None):
        """列出监控根目录下所有空目录（跳过内置排除目录）。"""
        roots = [root] if root else [r["path"] for r in fileindex.list_roots(only_enabled=True)]
        found = []
        for base in roots:
            if not base or not os.path.isdir(base):
                continue
            for dp, dns, fns in os.walk(base):
                parts = set(p.lower() for p in dp.split(os.sep))
                if parts & scanner.SKIP_DIRS:
                    continue
                if not dns and not fns:
                    found.append(dp)
        found.sort()
        return {"dirs": found}

    def trash_empty_dirs(self, paths):
        """把空目录送入系统回收站（仅接受真实存在的空目录，可恢复）。"""
        from backend import trash
        batch_id = db.new_batch_id()
        results = []
        for p in paths:
            try:
                if os.path.isdir(p) and not os.listdir(p):
                    trash.send_to_recycle_bin(p)
                    db.log_operation(batch_id, "trash", p, "回收站(空目录)")
                    results.append({"path": p, "ok": True})
                else:
                    results.append({"path": p, "ok": False, "error": "目录不存在或非空"})
            except Exception as e:
                results.append({"path": p, "ok": False, "error": str(e)})
        return {"batch_id": batch_id, "results": results}

    def add_watch_root(self, path):
        r = fileindex.add_root(path)
        if r.get("ok"):
            self.rescan_library()
        return r

    def remove_watch_root(self, root_id):
        fileindex.remove_root(root_id)
        if self.watch_engine:
            self.watch_engine.start()  # 重启观察者应用新根目录
        return {"ok": True}

    def is_image_file(self, path):
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        return ext in fileindex.IMAGE_EXTS

    # ---------- 模型管理（含删除权） ----------

    def delete_model(self, model):
        """删除本地 Ollama 模型，释放磁盘空间。"""
        client = OllamaClient()
        if not client.status()["running"]:
            return {"ok": False, "error": "Ollama 未运行"}
        result = client.delete_model(model)
        return result

    def list_models_detail(self):
        """带大小/时间的模型列表（设置页模型管理）。"""
        client = OllamaClient()
        st = client.status()
        models = []
        for name in st["models"]:
            info = st.get("model_info", {}).get(name, {})
            models.append({
                "name": name,
                "size": info.get("size", 0),
                "modified": info.get("modified", ""),
                "is_chat": name == st["chat_model"],
                "is_embed": name.startswith("bge") or "embed" in name,
                "is_vl": name.startswith("qwen2.5vl") or "vl" in name.split(":")[0],
            })
        return {"models": models, "running": st["running"],
                "chat_model": st["chat_model"]}

    # ---------- 功能开关 ----------

    def set_auto_kb(self, enabled):
        """自动知识库索引开关。开启时立即同步 auto 文件夹并首轮索引。"""
        db.set_setting("auto_kb", "1" if enabled else "0")
        if enabled:
            indexer.sync_auto_folders()
            self.index_all_auto_folders()
        return {"ok": True}

    def set_img_semantic(self, enabled):
        """图片语义搜索开关。开启时若无视觉模型返回提示。"""
        from backend import llm as llm_mod
        db.set_setting("img_semantic", "1" if enabled else "0")
        if enabled:
            if llm_mod.chat_available() and imgsearch.vl_ready():
                self.describe_backlog()
                return {"ok": True, "model_ready": True}
            return {"ok": True, "model_ready": False,
                    "vl_model": imgsearch.vl_model()}
        return {"ok": True}

    def img_semantic_status(self):
        return {"enabled": self._img_semantic_enabled(),
                "vl_model": imgsearch.vl_model(),
                "model_ready": imgsearch.vl_ready(),
                "described": db.img_index_stats(),
                "pending": len(imgsearch.pending_images(limit=100000))}

    # ---------- 开机自启 ----------

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _RUN_NAME = "FileButler"

    def _exe_path(self):
        if getattr(sys, "frozen", False):
            return os.path.abspath(sys.executable)
        return os.path.abspath(sys.argv[0])

    def autostart_status(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as k:
                val, _ = winreg.QueryValueEx(k, self._RUN_NAME)
                return {"enabled": True, "target": val}
        except OSError:
            return {"enabled": False, "target": None}

    # ---------- 数据目录管理 ----------

    def data_dir_status(self):
        """当前数据目录、大小、是否默认位置。"""
        cur = db.get_data_dir()
        size = 0
        for root, _, names in os.walk(cur):
            for n in names:
                try:
                    size += os.path.getsize(os.path.join(root, n))
                except OSError:
                    pass
        return {"current": cur, "default_dir": db.DEFAULT_DIR,
                "is_default": os.path.normcase(cur) == os.path.normcase(db.DEFAULT_DIR),
                "total_size": size, "db_path": db.DB_PATH}

    def open_data_dir(self):
        os.startfile(db.get_data_dir())
        return {"ok": True}

    def switch_data_dir(self, new_dir, overwrite=False):
        """搬迁数据库与缓存到新目录（旧目录保留为备份），需重启生效。"""
        try:
            # 先停写入源，缩小搬迁窗口
            if self.watch_engine:
                self.watch_engine.stop()
            result = db.switch_data_dir(
                new_dir, overwrite=overwrite,
                on_status=lambda m: self._emit("data_switch_status", m))
            if result.get("ok"):
                result["need_restart"] = True
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reset_data_dir(self, overwrite=False):
        """恢复默认数据目录（若默认位置仍有数据文件）。"""
        default = db.DEFAULT_DIR
        if db.get_data_dir() != default:
            # 默认目录若残留旧库，直接换指针即可；否则先搬回去
            if os.path.exists(os.path.join(default, "filebutler.db")):
                db.set_data_dir(None)
                db._apply_data_dir()
                return {"ok": True, "need_restart": True}
            return self.switch_data_dir(default, overwrite=overwrite)
        return {"ok": True}

    def restart_app(self):
        """重启应用（切换数据目录/恢复备份后生效用）。"""
        import subprocess
        if getattr(sys, "frozen", False):
            args = [sys.executable]
        else:
            args = [sys.executable, os.path.abspath(sys.argv[0])] + list(sys.argv[1:])
        subprocess.Popen(args)
        self._window.destroy()
        return {"ok": True}

    # ---------- 内置文件预览 ----------

    def preview_file(self, path):
        """返回预览信息：text/markdown/pdf/image/unknown。"""
        from backend.core.scanner import TEXT_EXTS
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        base = f"{self.thumb.port}" if self.thumb.port else "0"
        if ext == "pdf":
            return {"type": "pdf",
                    "url": f"http://127.0.0.1:{base}/preview?p={path}"}
        if ext in fileindex.IMAGE_EXTS:
            return {"type": "image",
                    "url": f"http://127.0.0.1:{base}/preview?p={path}"}
        if ext in ("docx", "xlsx", "pptx"):
            try:
                from backend import office_preview
                content = office_preview.to_html(path, ext)
                if content:
                    return {"type": "html", "content": content,
                            "name": os.path.basename(path)}
                return {"type": "unknown", "name": os.path.basename(path)}
            except Exception as e:
                return {"error": str(e), "name": os.path.basename(path)}
        if ext in TEXT_EXTS or ext == "doc":
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(500_000)
                kind = "markdown" if ext in ("md", "markdown") else "text"
                return {"type": kind, "content": content,
                        "name": os.path.basename(path)}
            except OSError as e:
                return {"error": str(e)}
        return {"type": "unknown", "name": os.path.basename(path)}

    # ---------- 问答历史 ----------

    def qa_new_session(self, title="新对话"):
        import time as _t
        conn = db.get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO qa_sessions(title, created_at, updated_at) VALUES(?,?,?)",
                (title[:60], _t.time(), _t.time()))
            conn.commit()
            return {"id": cur.lastrowid}
        finally:
            conn.close()

    def qa_list_sessions(self):
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, "
                "(SELECT COUNT(*) FROM qa_messages m WHERE m.session_id=s.id) AS n_msgs "
                "FROM qa_sessions s ORDER BY s.updated_at DESC LIMIT 100").fetchall()
            return {"sessions": [dict(r) for r in rows]}
        finally:
            conn.close()

    def qa_messages(self, session_id):
        import json as _json
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT role, content, sources, ts FROM qa_messages "
                "WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
            msgs = []
            for r in rows:
                m = {"role": r["role"], "content": r["content"], "ts": r["ts"]}
                if r["sources"]:
                    try:
                        m["sources"] = _json.loads(r["sources"])
                    except Exception:
                        pass
                msgs.append(m)
            return {"messages": msgs}
        finally:
            conn.close()

    def qa_save_message(self, session_id, role, content, sources=None):
        import json as _json
        import time as _t
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO qa_messages(session_id, role, content, sources, ts) "
                "VALUES(?,?,?,?,?)",
                (session_id, role, content,
                 _json.dumps(sources, ensure_ascii=False) if sources else None,
                 _t.time()))
            conn.execute("UPDATE qa_sessions SET updated_at=? WHERE id=?",
                         (_t.time(), session_id))
            # 首条用户消息自动成为会话标题
            if role == "user":
                first = conn.execute(
                    "SELECT COUNT(*) c FROM qa_messages WHERE session_id=?",
                    (session_id,)).fetchone()["c"]
                if first <= 1:
                    conn.execute("UPDATE qa_sessions SET title=? WHERE id=?",
                                 (content[:40], session_id))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def qa_delete_session(self, session_id):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM qa_messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM qa_sessions WHERE id=?", (session_id,))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    # ---------- 重复文件清理 ----------

    def dedupe_cleanup(self, groups, keep_rule="newest", dry_run=False):
        """重复文件安全清理：每组保留一个，其余移入待清理文件夹（可撤销）。"""
        return cleanup.dedupe_cleanup(groups, keep_rule=keep_rule, dry_run=dry_run)

    # ---------- 每周报告 ----------

    def weekly_report(self, force=False):
        from backend.core import report as report_mod
        r = report_mod.generate_weekly(force=force)
        if r.get("cached"):
            return r
        return report_mod.latest_report() or r

    def weekly_report_html(self):
        """周报导出为 HTML，返回 html 内容。"""
        from backend.core import report as report_mod
        html = report_mod.export_html()
        if not html:
            return {"error": "暂无周报数据"}
        return {"html": html}

    def save_weekly_report(self):
        """保存周报 HTML 到数据目录 reports/ 下，返回路径。"""
        from backend.core import report as report_mod
        html = report_mod.export_html()
        if not html:
            return {"error": "暂无周报数据"}
        r = report_mod.latest_report()
        ws = r["week_start"] if r else "unknown"
        out_dir = os.path.join(db.get_data_dir(), "reports")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"weekly-{ws}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        return {"ok": True, "path": out_path}

    # ---------- 备份 ----------

    def backup_now(self):
        try:
            path = db.backup_db()
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_backups(self):
        return {"backups": db.list_backups()}

    def restore_backup(self, backup_path):
        return db.restore_backup(backup_path)

    # ---------- 数据库维护 ----------

    _vacuum_running = False

    def db_maintenance_info(self):
        """库大小 / WAL 大小 / 备份数（设置页维护卡片）。"""
        sizes = db.db_file_sizes()
        return {"db_size": sizes["db"], "wal_size": sizes["wal"],
                "backups": len(db.list_backups()),
                "vacuum_running": Api._vacuum_running}

    def vacuum_db(self):
        """后台 VACUUM 压缩数据库（约需 2 倍库大小空闲磁盘），完成发事件。"""
        if Api._vacuum_running:
            return {"ok": False, "error": "压缩正在进行中"}

        def on_done(result):
            Api._vacuum_running = False
            self._emit("db_vacuum_done", result)

        Api._vacuum_running = True
        threading.Thread(target=lambda: db.vacuum(on_done=on_done),
                         daemon=True, name="fb-vacuum").start()
        return {"ok": True, "started": True}

    # ---------- 整理方案模板 ----------

    def list_templates(self):
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT id, name, target_root, images_by_year FROM organize_templates "
                "ORDER BY id DESC").fetchall()
            return {"templates": [dict(r) for r in rows]}
        finally:
            conn.close()

    def save_template(self, name, target_root, images_by_year=True):
        import time as _t
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO organize_templates(name, target_root, images_by_year, created_at) "
                "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "target_root=excluded.target_root, images_by_year=excluded.images_by_year",
                (name[:40], target_root, 1 if images_by_year else 0, _t.time()))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def delete_template(self, template_id):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM organize_templates WHERE id=?", (template_id,))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def set_autostart(self, enabled):
        """开机自启（HKCU Run 注册表，仅当前用户，无管理员权限要求）。"""
        import winreg
        try:
            if enabled:
                cmd = f'"{self._exe_path()}"'
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as k:
                    winreg.SetValueEx(k, self._RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    try:
                        winreg.DeleteValue(k, self._RUN_NAME)
                    except OSError:
                        pass
            return {"ok": True, "enabled": enabled}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    # ---------- 全局热键 ----------

    def hotkey_status(self):
        return {"enabled": db.get_setting("hotkey_enabled", "0") == "1",
                "hotkey": db.get_setting("hotkey_combo", "Alt+Space")}

    def set_hotkey(self, enabled, combo="Alt+Space"):
        db.set_setting("hotkey_enabled", "1" if enabled else "0")
        db.set_setting("hotkey_combo", combo)
        # 通知 main.py 热键线程
        self._emit("hotkey_changed", {"enabled": enabled, "combo": combo})
        return {"ok": True}

    # ---------- 自定义排除目录 ----------

    def list_excludes(self):
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT id, path, added_at FROM exclude_rules ORDER BY id").fetchall()
            return {"rules": [dict(r) for r in rows]}
        finally:
            conn.close()

    def add_exclude(self, path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return {"ok": False, "error": "不是有效目录"}
        import time as _t
        conn = db.get_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO exclude_rules(path,added_at) VALUES(?,?)",
                         (path, _t.time()))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def remove_exclude(self, rule_id):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM exclude_rules WHERE id=?", (rule_id,))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    # ---------- 收藏夹 ----------

    def list_favorites(self):
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT path, note, created_at FROM favorites ORDER BY created_at DESC").fetchall()
            return {"items": [dict(r) for r in rows]}
        finally:
            conn.close()

    def add_favorite(self, path, note=""):
        path = os.path.abspath(path)
        import time as _t
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO favorites(path,note,created_at) VALUES(?,?,?)",
                (path, note, _t.time()))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def remove_favorite(self, path):
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM favorites WHERE path=?", (os.path.abspath(path),))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def is_favorite(self, path):
        conn = db.get_conn()
        try:
            r = conn.execute("SELECT 1 FROM favorites WHERE path=?", (os.path.abspath(path),)).fetchone()
            return {"favorited": r is not None}
        finally:
            conn.close()
