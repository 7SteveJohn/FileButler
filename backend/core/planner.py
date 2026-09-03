"""整理计划生成：文件 → 目标路径映射（预览数据）。"""
import os

from backend.core import rules as rules_mod


def _unique_path(dst):
    """目标已存在时追加序号，绝不覆盖。"""
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    for i in range(1, 10000):
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
    return None


def _image_subdir(src, by_year):
    """图片可选按拍摄年份分子目录（读 EXIF，失败退回文件修改时间）。"""
    if not by_year:
        return ""
    year = None
    try:
        from PIL import Image
        with Image.open(src) as im:
            exif = im.getexif()
            # 0x9003 = DateTimeOriginal
            dt = exif.get(0x9003) or exif.get(0x0132)
        if dt and len(dt) >= 4:
            year = dt[:4]
    except Exception:
        pass
    if not year:
        year = "未知年份"
    return year


def build_plan(files, target_root, category_map=None, by_year_images=None, rules=None):
    """
    files: 扫描结果（含 ai_category 字段的行优先用 AI 结果）
    target_root: 整理目标根目录
    category_map: {path: (category, sub)} 显式覆盖（预览界面改过的）
    返回计划行列表：src, dst, category, sub, action(move/skip), size
    """
    rules = rules or rules_mod.load_user_rules()
    if by_year_images is None:
        by_year_images = os.environ.get("FB_IMAGES_BY_YEAR", "1") == "1"
    category_map = category_map or {}
    plan = []
    for fi in files:
        src = fi["path"]
        if category_map.get(src):
            cat, sub = category_map[src]
        elif fi.get("ai_category"):
            cat, sub = fi["ai_category"], ""
        else:
            hit = rules_mod.classify_by_ext(fi.get("ext"), rules)
            if hit:
                cat, sub = hit
            else:
                cat, sub = "其他", ""

        if cat == "图片" and by_year_images:
            y = _image_subdir(src, by_year_images)
            sub = y if sub == "" else sub  # 已有子目录(如 psd)时不叠加年份

        parts = [target_root, cat] + ([sub] if sub else []) + [fi["name"]]
        dst = os.path.join(*parts)

        # 已经在正确位置 → 跳过
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            plan.append({"src": src, "dst": dst, "category": cat, "sub": sub,
                         "action": "skip", "size": fi["size"]})
            continue
        # 目标已存在且内容是同一个文件 → 跳过（避免重复移动）
        if os.path.exists(dst) and os.path.getsize(dst) == fi["size"]:
            plan.append({"src": src, "dst": dst, "category": cat, "sub": sub,
                         "action": "skip", "size": fi["size"]})
            continue
        dst = _unique_path(dst)
        if dst is None:
            plan.append({"src": src, "dst": "", "category": cat, "sub": sub,
                         "action": "error", "size": fi["size"]})
            continue
        plan.append({"src": src, "dst": dst, "category": cat, "sub": sub,
                     "action": "move", "size": fi["size"]})
    return plan


def summarize(plan):
    """按类别汇总统计，供预览页顶部展示。"""
    out = {}
    for row in plan:
        if row["action"] != "move":
            continue
        c = row["category"]
        out.setdefault(c, {"count": 0, "size": 0})
        out[c]["count"] += 1
        # 防御性：脏数据 size 可能是 text
        try:
            s = int(row["size"] or 0)
        except (TypeError, ValueError):
            s = 0
        out[c]["size"] += s
    return out
