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
                moved += 1
            except Exception:
                failed += 1
    return {"moved": moved, "kept": kept, "failed": failed, "batch_id": batch_id,
            "dir": target_dir, "dry_run": dry_run}
