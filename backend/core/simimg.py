"""相似图片检测：dHash 感知哈希 + 汉明距离。

找「看起来一样」的图（缩放版、重新压缩、连拍近似帧），
返回结构与 core/dedupe.find_duplicates 兼容，可直接复用清理/撤销流程。
"""
import os
from collections import defaultdict

try:
    from PIL import Image
except ImportError:
    Image = None

# 参与检测的图片扩展名（与 fileindex.IMAGE_EXTS 一致）
SIM_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tiff", "ico"}


def dhash(path, hash_size=8):
    """感知哈希：resize 到 (hash_size+1, hash_size) 灰度，相邻像素比较 → 64bit 整数。
    缩放版 / 压缩版会在大多数位保持一致，汉明距离小。失败返回 None。"""
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
        px = im.load()
        h = 0
        for y in range(hash_size):
            for x in range(hash_size):
                h = (h << 1) | (1 if px[x, y] > px[x + 1, y] else 0)
        return h
    except Exception:
        return None


def hamming(a, b):
    return bin(a ^ b).count("1")


def find_similar(files, threshold=10, progress_cb=None):
    """
    files: 扫描结果列表（含 path/ext/size 的 dict）。
    threshold: 汉明距离阈值（0-64），越小越严格；默认 10 对缩放/压缩版有较好召回。
    返回 [{hash, size, paths}]，paths 首项为建议保留（最新）。
    """
    imgs = []
    for f in files:
        ext = (f.get("ext") or os.path.splitext(f["path"])[1].lstrip(".")).lower()
        if ext in SIM_IMAGE_EXTS:
            imgs.append(f)

    hashes = []
    total = len(imgs)
    for i, f in enumerate(imgs):
        if progress_cb and i % 10 == 0:
            progress_cb(i + 1, total)
        h = dhash(f["path"])
        if h is None:
            continue
        # 过滤低信息量哈希：纯色 / 渐变图 dHash 会退化为全 0 或全 1，
        # 任何两张这样的图都会"相似"，没有区分度
        ones = h.bit_count()
        if not (6 <= ones <= 58):
            continue
        hashes.append((f["path"], h))
    if len(hashes) < 2:
        return []

    # 双桶分桶（前 12 位 / 中间 12 位），任意一致才做两两比较，避免全量 O(n²)
    buckets = defaultdict(list)
    for p, h in hashes:
        buckets[(0, h >> 52)].append((p, h))
        buckets[(1, (h >> 40) & 0xFFF)].append((p, h))

    parent = {p: p for p, _ in hashes}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for items in buckets.values():
        for i in range(len(items)):
            pi, hi = items[i]
            for j in range(i + 1, len(items)):
                pj, hj = items[j]
                if hamming(hi, hj) <= threshold:
                    ri, rj = find(pi), find(pj)
                    if ri != rj:
                        parent[rj] = ri

    groups = defaultdict(list)
    for p, _ in hashes:
        groups[find(p)].append(p)

    result = []
    for idx, ps in enumerate(groups.values()):
        if len(ps) < 2:
            continue
        ps = sorted(set(ps), key=lambda p: (-_mtime(p), len(p)))
        try:
            size = os.path.getsize(ps[0])
        except OSError:
            size = 0
        result.append({"hash": f"sim:{idx}", "size": size, "paths": ps})
    result.sort(key=lambda g: -(g["size"] * (len(g["paths"]) - 1)))
    return result


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0
