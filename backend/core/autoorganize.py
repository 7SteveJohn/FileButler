"""自动整理（可选项）：新文件出现时按规则自动归类。

默认关闭。开启后两种模式：
- report：仅记录「建议移动到哪」，不碰文件（默认，守「绝不自动移动」原则）
- move：自动移动到目标目录并记入操作批次（可一键撤销）

目标目录在设置页指定；未设置或规则未命中时不动作。
"""
import os

from backend import db
from backend.core import executor, planner, rules as rules_mod


def status():
    return {
        "enabled": db.get_setting("auto_organize_enabled", "0") == "1",
        "mode": db.get_setting("auto_organize_mode", "report"),
        "root": db.get_setting("auto_organize_root", ""),
    }


def maybe_organize(path):
    """对单个新文件尝试自动整理；返回事件 dict 或 None。"""
    s = status()
    if not s["enabled"] or not s["root"] or not os.path.isdir(s["root"]):
        return None
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    hit = rules_mod.classify_by_ext(ext)
    if not hit:
        return None
    cat, sub = hit
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    files = [{"path": path, "name": os.path.basename(path),
              "ext": ext, "size": size, "category": cat}]
    plan = planner.build_plan(files, s["root"])
    if not plan or plan[0].get("action") != "move":
        return None
    row = plan[0]

    if s["mode"] == "report":
        return {"mode": "report", "path": path, "dst": row["dst"],
                "category": cat, "size": size}

    # move 模式：执行并记入批次（可撤销）
    try:
        res = executor.execute([row])
        ok = any(r.get("ok") for r in res.get("results", []))
        return {"mode": "move", "path": path, "dst": row["dst"],
                "category": cat, "size": size, "ok": ok,
                "batch_id": res.get("batch_id"),
                "error": None if ok else "移动失败（详见操作历史）"}
    except Exception as e:
        return {"mode": "move", "path": path, "dst": row["dst"],
                "category": cat, "size": size, "ok": False, "error": str(e)}
