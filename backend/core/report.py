"""每周文件报告：本周新增/大文件/类别分布，存库供 Dashboard 展示。"""
import datetime
import json
import time

from backend import db


def _safe_int(v, default=0):
    """脏数据防御：用户库历史 size 字段可能存了 text，无法 int() 时返回 0。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def _week_start(dt=None):
    """本周周一 0 点。"""
    dt = dt or datetime.datetime.now()
    monday = dt - datetime.timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def generate_weekly(force=False):
    """生成本周报告（幂等：同周已生成且非 force 则直接返回）。"""
    ws = _week_start()
    ws_str = ws.strftime("%Y-%m-%d")
    conn = db.get_conn()
    try:
        if not force:
            row = conn.execute(
                "SELECT data FROM weekly_reports WHERE week_start=?", (ws_str,)).fetchone()
            if row:
                return {"week_start": ws_str, "data": json.loads(row["data"]),
                        "cached": True}
    finally:
        conn.close()

    since = ws.timestamp()
    conn = db.get_conn()
    try:
        new_files = conn.execute(
            "SELECT path, name, category, size FROM file_index WHERE mtime>=? "
            "ORDER BY mtime DESC", (since,)).fetchall()
        big = conn.execute(
            "SELECT path, name, category, size FROM file_index ORDER BY size DESC LIMIT 10"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM file_index").fetchone()
    finally:
        conn.close()

    by_cat = {}
    for r in new_files:
        c = r["category"]
        # 防御性：脏数据 size 可能是 text
        try:
            s = int(r["size"] or 0)
        except (TypeError, ValueError):
            s = 0
        by_cat.setdefault(c, {"n": 0, "size": 0})
        by_cat[c]["n"] += 1
        by_cat[c]["size"] += s

    data = {
        "week_start": ws_str,
        "generated_at": time.time(),
        "total_files": total["c"],
        "total_size": total["s"],
        "new_count": len(new_files),
        "new_size": sum(_safe_int(r["size"]) for r in new_files),
        "by_category": by_cat,
        "new_sample": [dict(r) for r in new_files[:20]],
        "big_files": [dict(r) for r in big],
    }

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO weekly_reports(week_start, created_at, data) VALUES(?,?,?) "
            "ON CONFLICT(week_start) DO UPDATE SET created_at=excluded.created_at, "
            "data=excluded.data",
            (ws_str, time.time(), json.dumps(data, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return {"week_start": ws_str, "data": data, "cached": False}


def latest_report():
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT week_start, data FROM weekly_reports ORDER BY week_start DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {"week_start": row["week_start"], "data": json.loads(row["data"])}
    finally:
        conn.close()


def _fmt_size(n):
    if n is None:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}"


def export_html(report_data=None, week_start=None):
    """生成周报 HTML 字符串。report_data 可从 generate_weekly 或 latest_report 获取。"""
    if report_data is None:
        r = latest_report()
        if not r:
            return None
        week_start = r["week_start"]
        report_data = r["data"]
    d = report_data
    cat_rows = ""
    for cat, v in (d.get("by_category") or {}).items():
        cat_rows += f'<tr><td>{cat}</td><td>+{v["n"]}</td><td>{_fmt_size(v["size"])}</td></tr>\n'

    sample_rows = ""
    for f in (d.get("new_sample") or [])[:10]:
        sample_rows += f'<tr><td>{f["name"]}</td><td>{f.get("category","")}</td><td>{_fmt_size(f["size"])}</td></tr>\n'

    big_rows = ""
    for f in (d.get("big_files") or [])[:5]:
        big_rows += f'<tr><td>{f["name"]}</td><td>{f.get("category","")}</td><td>{_fmt_size(f["size"])}</td></tr>\n'

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>FileButler 周报 · {week_start}</title>
<style>
body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; background: #fafafa; }}
h1 {{ color: #18a058; border-bottom: 2px solid #18a058; padding-bottom: 8px; }}
h2 {{ color: #555; margin-top: 28px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 16px; }}
th, td {{ border: 1px solid #e0e0e0; padding: 6px 10px; text-align: left; }}
th {{ background: #f5f5f5; }}
.stat {{ display: inline-block; background: #18a058; color: white; border-radius: 8px; padding: 12px 20px; margin: 6px; text-align: center; min-width: 120px; }}
.stat b {{ display: block; font-size: 1.4em; }}
.stat span {{ font-size: 0.85em; opacity: 0.9; }}
.footer {{ margin-top: 30px; color: #999; font-size: 12px; }}
</style></head><body>
<h1>📂 FileButler 周报</h1>
<p style="color:#666">周起始：{week_start} · 由 FileButler 自动生成</p>

<div style="margin:16px 0">
<div class="stat"><b>{d.get("new_count", 0)}</b><span>本周新增</span></div>
<div class="stat"><b>{_fmt_size(d.get("new_size", 0))}</b><span>新增体积</span></div>
<div class="stat"><b>{d.get("total_files", 0)}</b><span>编目总数</span></div>
<div class="stat"><b>{_fmt_size(d.get("total_size", 0))}</b><span>总体积</span></div>
</div>

<h2>类别分布</h2>
<table><tr><th>类别</th><th>新增数</th><th>新增体积</th></tr>
{cat_rows}</table>

{"<h2>本周新增文件</h2><table><tr><th>文件名</th><th>类别</th><th>大小</th></tr>" + sample_rows + "</table>" if sample_rows else ""}

{"<h2>体积排行 TOP</h2><table><tr><th>文件名</th><th>类别</th><th>大小</th></tr>" + big_rows + "</table>" if big_rows else ""}

<p class="footer">FileButler · 本地智能文件管家 · 所有数据仅存本机</p>
</body></html>"""
