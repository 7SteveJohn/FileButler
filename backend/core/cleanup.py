"""重复文件安全清理：不删除，移入「待清理」文件夹，全程可撤销。"""
import os
import shutil
import time

from backend import db


def cleanup_dir():
    return os.path.join(db.get_data_dir(), "待清理")


def dedupe_cleanup(groups, keep_rule="newest", dry_run=False, keep_paths=None):
    """
    groups: [{paths: [...]}, ...] 每组保留一个，其余移入 待清理/<时间戳>/
    keep_rule: newest（最新）/ oldest（最早）/ shortest（路径最短）
    keep_paths: 用户在对比预览里亲自点选的保留项；命中本组则优先于 keep_rule，
                没命中（文件已不在等）时照旧按规则选——不把「指定失效」变成误删。
    返回 {moved: n, kept: n, failed: n, batch_id, dir}
    """
    batch_id = db.new_batch_id()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_dir = os.path.join(cleanup_dir(), stamp)
    pinned = set(keep_paths or ())

    def key_fn(p):
        st = os.stat(p)
        if keep_rule == "oldest":
            return (st.st_mtime, len(p))
        if keep_rule == "shortest":
            return (len(p), -st.st_mtime)
        return (-st.st_mtime, len(p))  # newest

    moved = kept = failed = 0
    for group in groups:
        paths = [p for p in group.get("paths", []) if os.path.exists(p)]
        if len(paths) < 2:
            continue
        chosen = [p for p in paths if p in pinned]
        if chosen:
            keep = chosen[0]
            rest = [p for p in paths if p != keep]
        else:
            paths_by_key = sorted(paths, key=key_fn)
            keep = paths_by_key[0]
            rest = paths_by_key[1:]
        kept += 1
        for p in rest:
            if dry_run:
                moved += 1
                continue
            try:
                rel = p.replace(":", "_", 1) if len(p) > 1 and p[1] == ":" else p
                dst = os.path.join(target_dir, rel.lstrip(os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                final = dst
                i = 1
                while os.path.exists(final):
                    base, ext = os.path.splitext(dst)
                    final = f"{base} ({i}){ext}"
                    i += 1
                shutil.move(p, final)
                db.log_operation(batch_id, "trash", p, final)
                try:
                    from backend import fileindex
                    fileindex.remove_file(p)
                except Exception:
                    pass
                moved += 1
            except Exception:
                failed += 1
    return {"moved": moved, "kept": kept, "failed": failed, "batch_id": batch_id,
            "dir": target_dir, "dry_run": dry_run}


def _tree_stats(path):
    """统计目录（或单文件）下的文件总数与总字节数。"""
    if os.path.isfile(path):
        try:
            return 1, os.path.getsize(path)
        except OSError:
            return 0, 0
    files = size = 0
    for root, _, names in os.walk(path):
        for fn in names:
            try:
                size += os.path.getsize(os.path.join(root, fn))
                files += 1
            except OSError:
                pass
    return files, size


def list_quarantine():
    """列出「待清理」隔离区内的所有批次目录与总占用。"""
    base = os.path.abspath(cleanup_dir())
    if not os.path.isdir(base):
        return {"dir": base, "items": [], "total_files": 0, "total_size": 0}
    items = []
    total_files = total_size = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return {"dir": base, "items": [], "total_files": 0, "total_size": 0}
    for ent in entries:
        try:
            st = ent.stat()
            fcnt, bcnt = _tree_stats(ent.path)
            if fcnt == 0 and ent.is_dir():
                # 空壳批次目录也列出，方便用户一键清掉
                pass
            items.append({
                "name": ent.name,
                "path": ent.path,
                "files": fcnt,
                "size": bcnt,
                "mtime": st.st_mtime,
            })
            total_files += fcnt
            total_size += bcnt
        except OSError:
            continue
    items.sort(key=lambda x: (-x["mtime"], x["name"]))
    return {"dir": base, "items": items, "total_files": total_files,
            "total_size": total_size}


def purge_quarantine(names=None):
    """将待清理区指定批次（names=None 时全部）送入系统回收站，释放应用数据目录空间。

    安全边界：仅允许操作 cleanup_dir() 的直接子项，拒绝任何跨目录名称，
    且一律走系统回收站（SHFileOperationW FOF_ALLOWUNDO），绝不硬删。
    """
    from backend import trash
    base = os.path.abspath(cleanup_dir())
    if not os.path.isdir(base):
        return {"ok": True, "purged": 0, "freed_bytes": 0, "failed": 0, "results": []}
    if names is None:
        try:
            targets = list(os.listdir(base))
        except OSError as e:
            return {"ok": False, "error": str(e), "purged": 0, "freed_bytes": 0,
                    "failed": 0, "results": []}
    else:
        targets = [str(x) for x in (names or []) if x]

    batch_id = db.new_batch_id()
    purged = freed_bytes = failed = 0
    results = []
    base_norm = os.path.normcase(base)
    for name in targets:
        # 严格校验只允许单层子项名
        if os.path.basename(name) != name or name in (".", ".."):
            failed += 1
            results.append({"name": name, "ok": False, "error": "非法的批次名称"})
            continue
        full = os.path.abspath(os.path.join(base, name))
        if os.path.normcase(os.path.dirname(full)) != base_norm or not os.path.exists(full):
            failed += 1
            results.append({"name": name, "ok": False, "error": "批次不存在"})
            continue
        _, bcnt = _tree_stats(full)
        try:
            trash.send_to_recycle_bin(full)
            db.log_operation(batch_id, "trash", full, "回收站(待清理)")
            purged += 1
            freed_bytes += bcnt
            results.append({"name": name, "ok": True, "size": bcnt})
        except Exception as e:
            failed += 1
            results.append({"name": name, "ok": False, "error": str(e)})
    return {"ok": failed == 0, "batch_id": batch_id, "purged": purged,
            "freed_bytes": freed_bytes, "failed": failed, "results": results}
