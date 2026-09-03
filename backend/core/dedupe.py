"""重复文件检测：按大小分组 → 头部哈希 → 全量 SHA-256 三级过滤，避免全盘哈希。"""
import hashlib
import os
from collections import defaultdict


def _hash(path, length=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if length is None:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        else:
            h.update(f.read(length))
    return h.hexdigest()


def find_duplicates(files, progress_cb=None):
    """
    files: 扫描结果列表。返回重复组列表：
    [{"hash": ..., "size": ..., "paths": [p1, p2, ...]}, ...]
    每组按路径排序，保留第一个为建议保留项（前端标注）。
    """
    by_size = defaultdict(list)
    for fi in files:
        if fi["size"] > 0:
            by_size[fi["size"]].append(fi["path"])

    candidates = [paths for size, paths in by_size.items() if len(paths) > 1]
    total = len(candidates)
    groups = defaultdict(list)
    for i, paths in enumerate(candidates):
        if progress_cb and i % 20 == 0:
            progress_cb(i + 1, total)
        size = os.path.getsize(paths[0])
        head_len = min(size, 65536)
        by_head = defaultdict(list)
        for p in paths:
            try:
                by_head[_hash(p, head_len)].append(p)
            except OSError:
                continue
        for head, hpaths in by_head.items():
            if len(hpaths) < 2:
                continue
            for p in hpaths:
                try:
                    full = _hash(p)
                except OSError:
                    continue
                groups[full].append(p)

    result = []
    for h, paths in groups.items():
        if len(paths) > 1:
            result.append({
                "hash": h[:16],
                "size": os.path.getsize(paths[0]),
                "paths": sorted(set(paths)),
            })
    result.sort(key=lambda g: -(g["size"] * (len(g["paths"]) - 1)))
    return result
