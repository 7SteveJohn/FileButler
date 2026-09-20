"""计划执行与撤销：所有移动操作写日志，可整体回滚。"""
import os
import shutil

from backend import db


def execute(plan_rows, batch_id=None):
    """
    执行勾选的计划行（action=move）。返回每行结果。
    安全约束：目标在目标根目录内创建；源不存在/目标被占用均记为 failed 而非中断。
    """
    batch_id = batch_id or db.new_batch_id()
    results = []
    for row in plan_rows:
        if row.get("action") != "move":
            continue
        src, dst = row["src"], row["dst"]
        try:
            if not os.path.exists(src):
                raise FileNotFoundError("源文件不存在")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # 目标路径在执行期间被占用 → 换名重试一次
            if os.path.exists(dst):
                base, ext = os.path.splitext(dst)
                i = 1
                while os.path.exists(dst) and i < 1000:
                    dst = f"{base} ({i}){ext}"
                    i += 1
            shutil.move(src, dst)
            db.log_operation(batch_id, "move", src, dst)
            results.append({"src": src, "dst": dst, "ok": True})
        except Exception as e:
            results.append({"src": src, "dst": dst, "ok": False, "error": str(e)})
    return {"batch_id": batch_id, "results": results}


def _looks_like_path(dst):
    """dst 是不是一个真实路径。进系统回收站的操作 dst 记的是「回收站」字面量。"""
    d = dst or ""
    return os.sep in d or (os.altsep or "") in d or bool(os.path.splitdrive(d)[0])


def _restorable(action, dst):
    """哪些操作能由应用自己搬回来。

    move/rename 两端都是真实路径；trash 分两种——移进「待清理」目录的（dst 是路径）
    可以原样搬回，进系统回收站的只能由用户自己从回收站还原。copy 永远跳过：
    撤销副本等于删副本，那是破坏性操作。
    """
    if action not in ("move", "rename", "trash"):
        return False
    if action == "trash" and not _looks_like_path(dst):
        return False
    return True


def undo(batch_id):
    """按日志逆序撤销一个批次的可逆操作。

    不可逆的部分会如实计数返回：copy 计入 skipped，进系统回收站的 trash 计入
    recycle_bin（这些只能由用户从回收站还原）。
    """
    ops = db.list_operations(batch_id)
    undone = failed = skipped = in_bin = 0
    for op in reversed(ops):
        src, dst, action = op["src"], op["dst"], op["action"]
        if not _restorable(action, dst):
            if action == "trash":
                in_bin += 1
            else:
                skipped += 1
            continue
        try:
            if not os.path.exists(dst):
                raise FileNotFoundError(f"目标不存在：{dst}")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            final_src = src
            if os.path.exists(src):
                base, ext = os.path.splitext(src)
                i = 1
                while os.path.exists(final_src) and i < 1000:
                    final_src = f"{base} ({i}){ext}"
                    i += 1
            shutil.move(dst, final_src)
            undone += 1
        except Exception:
            failed += 1
    if failed == 0:
        db.mark_undone(batch_id)
    return {"batch_id": batch_id, "undone": undone, "failed": failed,
            "skipped": skipped, "recycle_bin": in_bin}


def redo(batch_id):
    """重做一个已撤销的批次：把文件再次移回整理后的位置（「取消撤销」）。"""
    conn = db.get_conn()
    try:
        ops = conn.execute(
            "SELECT src, dst, action FROM operations WHERE batch_id=? AND undone=1 "
            "ORDER BY id", (batch_id,)).fetchall()
    finally:
        conn.close()
    redone, failed = 0, 0
    for op in ops:
        if not _restorable(op["action"], op["dst"]):
            continue  # copy 与进系统回收站的操作不参与重做
        src, dst = op["src"], op["dst"]
        try:
            if not os.path.exists(src):
                raise FileNotFoundError(f"文件不在原位置：{src}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            final_dst = dst
            if os.path.exists(dst):
                base, ext = os.path.splitext(dst)
                i = 1
                while os.path.exists(final_dst) and i < 1000:
                    final_dst = f"{base} ({i}){ext}"
                    i += 1
            shutil.move(src, final_dst)
            redone += 1
        except Exception:
            failed += 1
    if failed == 0:
        conn = db.get_conn()
        try:
            conn.execute("UPDATE operations SET undone=0 WHERE batch_id=?", (batch_id,))
            conn.commit()
        finally:
            conn.close()
    return {"batch_id": batch_id, "redone": redone, "failed": failed}
