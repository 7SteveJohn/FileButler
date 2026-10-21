"""检索质量/性能基线评测（P0，UPGRADE_PLAN.md §2.1）。

在一次性 APPDATA 中构建 12 篇确定性合成文档（24 题：语义改写 + 精确实体各半），
分通道评测 hit@8 / 首命中排名 / 平均延迟：
  keyword — db.keyword_search（LIKE，现有降级通道，无模型可用）
  vector  — VectorIndex + bge-m3（需 Ollama，不可用时跳过）
  float32 — float32 精度模拟（VectorIndex 修 P2 精度后的预期速度）

第二段是规模延迟基准：向 chunks 表灌 N 条随机向量（默认 20000），
测 VectorIndex 全量重载耗时、稳态查询延迟、LIKE 关键词延迟。

用法：.venv/Scripts/python tests/eval_retrieval.py [--scale 20000]
输出末尾打印 BASELINE_JSON 供记录。全程不触碰真实数据库。
"""
import json
import os
import sys
import tempfile
import time

# 必须在导入 backend 前隔离 APPDATA（db 模块在 import 时读取）
_SANDBOX = tempfile.mkdtemp(prefix="fb_captest_eval_")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend import db, llm
from backend.knowledge import chunker, rag

DIM = 1024

# (文件名, 正文)。每篇含可被改写提问命中的事实与可精确匹配的实体。
DOCS = [
    ("财务报销制度", "差旅住宿标准：一线城市每晚 500 元，其他城市每晚 400 元。"
     "员工需在出差返回后 15 天内提交报销单，逾期不予受理。交通费用按高铁二等座标准报销，"
     "市内交通凭票据实报销。所有报销必须提供发票原件，复印件无效。"),
    ("星桥项目结项报告", "星桥项目（编号 XQ-2023-11）于 2024 年 3 月 15 日通过验收，"
     "项目结项金额 47 万元，项目负责人陈婷。项目完成了网关联调与压力测试两项核心交付物，"
     "客户方恒信集团的对接人为信息部经理赵磊。"),
    ("机房巡检规程", "机房巡检每两周执行一次，检查项包括 UPS 电池状态、空调湿度与门禁记录。"
     "UPS 电池组计划于 2025 年 6 月整体更换。每季度进行一次断电应急演练，"
     "演练期间核心业务系统切换至柴油发电机供电。A3 区门禁卡仅限运维组持有。"),
    ("采购部年度预算", "本年度服务器采购预算 120 万元，用于扩容渲染农场。"
     "显示器单品采购价上限 1500 元，超过需走特批流程。办公用品每季度集中采购一次，"
     "单次超过 5 万元的合同必须三方比价。"),
    ("恒信集团拜访纪要", "拜访时间 2024 年 5 月 20 日，恒信集团 CTO 王峰率队参加。"
     "对方最关注数据本地化部署能力，要求全部数据不出客户内网。"
     "初步方案为试点 50 套，报价在标准价基础上打 8 折，有效期至 6 月底。"),
    ("研发部代码规范", "Python 代码遵循 PEP8，行宽上限 100 字符。提交信息格式为"
     "「模块: 动作 + 对象」，例如 db: 修复连接泄漏。每日晨会时间 10:00，"
     "时长不超过 15 分钟。代码合并前必须通过单元测试与静态检查。"),
    ("新员工入职指南", "新员工工位默认安排在 B 座 5 层开放区。VPN 权限需到 IT 服务台"
     "填写申请单，一般 2 个工作日开通。企业邮箱签名按统一模板配置，"
     "包含姓名、部门与座机号。入职首周需完成信息安全在线课程。"),
    ("数据库运维手册", "MySQL 主从延迟告警阈值设为 5 秒，超过即短信通知值班人。"
     "全量备份每日凌晨 2 点执行，保留 7 天。慢查询日志保留 30 天，"
     "单条超过 1 秒的查询需要登记优化。变更窗口为每周三 22:00 之后。"),
    ("市场部三季度活动方案", "新品发布会定于 9 月 18 日在会展中心举行，场地预算 8 万元。"
     "拟邀请 20 家行业媒体，传播主题为「把智能装进每一台电脑」。"
     "线上渠道同步开启预约直播，目标观看人次 5 万。"),
    ("消防安全检查表", "灭火器年检到期时间为 2025 年 4 月，检查人应核对压力表指针在绿区。"
     "疏散通道净宽度不得小于 1.4 米，安全出口指示灯保持常亮。"
     "消防控制室值班电话为 6119，夜间双人值守。"),
    ("蓝湖物流服务合同", "合同编号 LH-2025-077，乙方蓝湖物流有限公司，"
     "运费结算周期 T+7，逾期按日千分之三计违约金。乙方负责人孙倩，"
     "结算联系人电话 8527-4432。合同有效期至 2026 年 12 月 31 日。"),
    ("FileButler 产品路线图", "1.3 版本目标：混合检索上线、相似图片检测进入整理页、"
     "回收站集成系统原生接口。远期方向：对话式整理、应用自更新、界面国际化。"
     "技术底座保持本地优先，向量检索不依赖任何云服务。"),
]

# (问题, 目标文件名, 类型)。paraphrase=语义改写，exact=精确实体。
QUESTIONS = [
    ("出差住酒店一晚上最多能报多少钱", "财务报销制度", "paraphrase"),
    ("蓝湖物流的合同编号是多少", "蓝湖物流服务合同", "exact"),
    ("服务器采购这一年准备花多少钱", "采购部年度预算", "paraphrase"),
    ("UPS 电池什么时候换", "机房巡检规程", "exact"),
    ("星桥项目最后验收通过了吗", "星桥项目结项报告", "paraphrase"),
    ("XQ-2023-11 的负责人是谁", "星桥项目结项报告", "exact"),
    ("恒信那边对部署方式有什么要求", "恒信集团拜访纪要", "paraphrase"),
    ("LH-2025-077 的运费怎么结算", "蓝湖物流服务合同", "exact"),
    ("新同事的工位安排在哪里", "新员工入职指南", "paraphrase"),
    ("数据库备份每天几点跑", "数据库运维手册", "exact"),
    ("发布会场地花了多少预算", "市场部三季度活动方案", "paraphrase"),
    ("主从延迟多少会告警", "数据库运维手册", "exact"),
    ("入职第一周要做什么培训", "新员工入职指南", "paraphrase"),
    ("灭火器下次年检是什么时候", "消防安全检查表", "exact"),
    ("写代码提交信息有什么格式要求", "研发部代码规范", "paraphrase"),
    ("疏散通道最窄要多宽", "消防安全检查表", "exact"),
    ("客户说数据不能出内网怎么办", "恒信集团拜访纪要", "paraphrase"),
    ("晨会是每天几点", "研发部代码规范", "exact"),
    ("显示器最多按什么价格买", "采购部年度预算", "paraphrase"),
    ("FileButler 下一版做什么功能", "FileButler 产品路线图", "paraphrase"),
    ("显示器价格超过多少要特批", "采购部年度预算", "exact"),
    ("发布会什么时候办", "市场部三季度活动方案", "paraphrase"),
    ("数据库慢查询日志存多久", "数据库运维手册", "exact"),
    ("报销单最晚什么时候交", "财务报销制度", "paraphrase"),
    # 纯实体短查询：向量通道的天然弱项（编号/号码本身无语义），词法通道的主场
    ("6119 是什么电话", "消防安全检查表", "exact"),
    ("8527-4432 是谁的电话", "蓝湖物流服务合同", "exact"),
    ("T+7 是什么结算周期", "蓝湖物流服务合同", "exact"),
    ("A3 区门禁卡谁能拿", "机房巡检规程", "exact"),
    ("XQ-2023-11 结项金额多少", "星桥项目结项报告", "exact"),
]


def seed_corpus(embed_ok, client):
    """把合成文档写入一次性库，返回 {(file_path, seq): vec}。"""
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO kb_folders(path, added_at, source) VALUES(?, ?, 'manual')",
                     (os.path.join(_SANDBOX, "kb"), time.time()))
        folder_id = conn.execute("SELECT id FROM kb_folders LIMIT 1").fetchone()["id"]
        vecs = {}
        for title, body in DOCS:
            path = title + ".md"
            cur = conn.execute(
                "INSERT INTO kb_files(folder_id, path, mtime, size) VALUES(?, ?, ?, ?)",
                (folder_id, path, time.time(), len(body)))
            fid = cur.lastrowid
            pieces = chunker.chunk_text(body)
            embeds = client.embed(pieces) if embed_ok else [None] * len(pieces)
            for seq, (piece, v) in enumerate(zip(pieces, embeds)):
                blob = np.asarray(v, dtype=np.float32).tobytes() if v is not None else None
                cur2 = conn.execute(
                    "INSERT INTO chunks(file_id, seq, text, btext, vec) VALUES(?, ?, ?, ?, ?)",
                    (fid, seq, piece, db.chunk_btext(piece), blob))
                if v is not None:
                    vecs[(path, seq)] = np.asarray(v, dtype=np.float32)
        conn.commit()
        return vecs
    finally:
        conn.close()


def eval_channel(rank_fn, questions):
    """rank_fn(query, k) -> [(file_path, score)]。统计 hit@8 / 首命中排名 / 延迟。"""
    hits, ranks, lats = 0, [], []
    for q, target, kind in questions:
        t0 = time.perf_counter()
        ranked = rank_fn(q, 8)
        lats.append((time.perf_counter() - t0) * 1000)
        paths = [p for p, s in ranked]
        if target + ".md" in paths:
            hits += 1
            ranks.append(paths.index(target + ".md") + 1)
        else:
            ranks.append(None)
    n = len(questions)
    good = [r for r in ranks if r]
    return {"hit8": round(hits / n, 3),
            "mrr": round(sum(1.0 / r for r in good) / n, 3),
            "lat_ms": round(sum(lats) / len(lats), 1)}


def rank_keyword(q, k):
    return [(r["file_path"], 0) for r in db.keyword_search(q, k=k)]


def rank_vector(q, k):
    qv = llm.get_embed_client().embed_one(q)
    hits = db.vec_index.search(qv, k=k)
    rows = db.search_chunks_by_ids([h[0] for h in hits]) if hits else {}
    return [(rows[h[0]]["file_path"], h[1]) for h in hits if h[0] in rows]


def rank_fts(q, k):
    ids = db.chunk_fts_search(q, k=k)
    rows = db.search_chunks_by_ids(ids) if ids else {}
    return [(rows[i]["file_path"], 0) for i in ids if i in rows]


def rank_hybrid(q, k):
    picked, mode, _qv = rag._retrieve(q, k)
    rows = db.search_chunks_by_ids([cid for cid, s in picked]) if picked else {}
    return [(rows[cid]["file_path"], s) for cid, s in picked if cid in rows]


def scale_benchmark(n):
    """向 chunks 灌 n 条随机归一化向量，测 VectorIndex 重载/查询/关键词延迟。"""
    rng = np.random.default_rng(7)
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO kb_files(folder_id, path, mtime, size) "
                     "VALUES(999999, 'scale_bench.md', 0, 0)")
        fid = conn.execute("SELECT id FROM kb_files WHERE path='scale_bench.md'").fetchone()["id"]
        mat = rng.standard_normal((n, DIM)).astype(np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        rows = [(fid, i, "bench", mat[i].tobytes()) for i in range(n)]
        t0 = time.perf_counter()
        conn.executemany(
            "INSERT INTO chunks(file_id, seq, text, vec) VALUES(?, ?, ?, ?)", rows)
        conn.commit()
        print(f"[scale] 灌入 {n} 条随机向量: {time.perf_counter()-t0:.1f}s")
    finally:
        conn.close()
    db.vec_index.invalidate()

    t0 = time.perf_counter()
    db.vec_index.search(mat[0], k=8)  # 首查含全量载入
    load_ms = (time.perf_counter() - t0) * 1000

    lats = []
    for i in range(20):
        t0 = time.perf_counter()
        db.vec_index.search(mat[i], k=8)
        lats.append((time.perf_counter() - t0) * 1000)
    q_ms = float(np.mean(lats))

    kw = []
    for w in ("备份", "结算", "演练", "预算", "规范"):
        t0 = time.perf_counter()
        db.keyword_search(w, k=8)
        kw.append((time.perf_counter() - t0) * 1000)
    return {"n": n, "reload_ms": round(load_ms, 1),
            "vec_ms": round(q_ms, 1),
            "keyword_ms": round(float(np.mean(kw)), 1)}


# 多轮追问评测：(第一问, 助手答案要点, 追问, 目标文件)。追问原词带指代
# （「那/他/超过上限」），脱离上下文检索必落空或偏航——先用真实对话模型
# 走 rag._rewrite_query 改写再检索，与原词直查对照，量化改写的真实收益。
# 对话模型不可用时整段跳过（套件在无 Ollama 环境自足）。
FOLLOWUPS = [
    ("出差住宿报销标准是多少", "一线城市每晚 500 元。", "那其他城市呢", "财务报销制度"),
    ("星桥项目的负责人是谁", "项目负责人是陈婷。", "客户那边对接的是谁", "星桥项目结项报告"),
    ("恒信集团这次谁来访了", "CTO 王峰率队参加。", "他们首批要买多少套", "恒信集团拜访纪要"),
    ("蓝湖物流的运费怎么结算", "按 T+7 周期结算。", "那负责人的电话多少", "蓝湖物流服务合同"),
    ("公司买显示器价格上限是多少", "单品上限 1500 元。", "超过上限要走什么流程", "采购部年度预算"),
    ("数据库备份每天几点执行", "凌晨 2 点。", "备份保留多长时间", "数据库运维手册"),
    ("机房 UPS 电池什么时候换", "2025 年 6 月整体更换。", "断电应急演练多久搞一次", "机房巡检规程"),
    ("灭火器年检什么时候到期", "2025 年 4 月。", "控制室值班电话是多少", "消防安全检查表"),
]


def seed_fillers(client, n=120):
    """确定性合成 n 篇干扰文档并入库向量化。

    12 篇语料下 hit@8 的随机基线高达 67%（8/12），追问对照看不出改写收益；
    扩到 130+ 篇后 hit@8 基线降到 6%，指标才有区分度。标题与正文由索引 i
    确定性生成（不引随机数，重跑可复现）。"""
    words = ("条码 打印机 服务器 会议室 班车 工牌 投影仪 饮水机 快递 档案室 "
             "考勤 门禁 电梯 空调 加湿器 网线 墨盒 硒鼓 订书机 碎纸机").split()
    topics = ("周报 会议纪要 合同 说明书 清单 日程 笔记 方案 记录 手册").split()
    conn = db.get_conn()
    try:
        folder_id = conn.execute("SELECT id FROM kb_folders LIMIT 1").fetchone()["id"]
        pieces = []
        for i in range(n):
            title = f"{topics[i % len(topics)]}杂项{i:03d}"
            body = "。".join(
                f"关于{words[(i + j * 3) % len(words)]}的说明第{j + 1}条：常规事项，"
                f"按流程办理即可" for j in range(3)) + "。"
            path = f"filler/{title}.md"
            cur = conn.execute(
                "INSERT INTO kb_files(folder_id, path, mtime, size) VALUES(?, ?, ?, ?)",
                (folder_id, path, time.time(), len(body)))
            pieces.append((cur.lastrowid, body, path))
        conn.commit()
        vecs = client.embed([b for _, b, _ in pieces])
        for (fid, body, path), v in zip(pieces, vecs):
            conn.execute(
                "INSERT INTO chunks(file_id, seq, text, btext, vec) VALUES(?, ?, ?, ?, ?)",
                (fid, 0, body, db.chunk_btext(body),
                 np.asarray(v, dtype=np.float32).tobytes()))
        conn.commit()
    finally:
        conn.close()
    db.vec_index.invalidate()


def eval_followup():
    """追问检索对照：原词直查 vs 改写后检索（hybrid 通道，hit@8/MRR）。"""
    client = llm.get_chat_client()
    out = {"n": len(FOLLOWUPS), "raw": {"hits": 0, "mrr": 0.0},
           "rewrite": {"hits": 0, "mrr": 0.0}, "rewritten": 0, "fallback": 0}
    for q1, a1, q2, target in FOLLOWUPS:
        history = [{"role": "user", "content": q1},
                   {"role": "assistant", "content": a1}]
        rewritten = rag._rewrite_query(client, q2, history)
        if rewritten:
            out["rewritten"] += 1
        else:
            out["fallback"] += 1
        for key, query in (("raw", q2), ("rewrite", rewritten or q2)):
            paths = [p for p, s in rank_hybrid(query, 8)]
            ok = 1 if target + ".md" in paths else 0
            rank = paths.index(target + ".md") + 1 if ok else 0
            out[key]["hits"] += ok
            out[key]["mrr"] += (1.0 / rank) if ok else 0.0
    n = out["n"]
    for key in ("raw", "rewrite"):
        out[key]["hit8"] = round(out[key].pop("hits") / n, 3)
        out[key]["mrr"] = round(out[key]["mrr"] / n, 3)
    return out


def main():
    args = sys.argv[1:]
    scale = 20000
    if "--scale" in args:
        scale = int(args[args.index("--scale") + 1])
    no_scale = "--no-scale" in args

    db.init_db()
    embed_ok = llm.embed_available()
    client = llm.get_embed_client()
    print(f"[eval] 嵌入通道可用: {embed_ok}（{llm.embed_status()['reason']}）")

    if embed_ok:
        t0 = time.perf_counter()
        vecs = seed_corpus(True, client)
        print(f"[eval] 语料就绪: {len(DOCS)} 篇 / {len(vecs)} 块已向量化 "
              f"({time.perf_counter()-t0:.1f}s)")
    else:
        vecs = seed_corpus(False, client)
        print(f"[eval] 语料就绪: {len(DOCS)} 篇（无嵌入，vector 通道跳过）")

    result = {"questions": len(QUESTIONS), "embed_ok": embed_ok}
    channels = [("keyword", rank_keyword)]
    if embed_ok:
        channels.append(("vector", rank_vector))
    channels += [("fts", rank_fts), ("hybrid", rank_hybrid)]
    for name, fn in channels:
        db.vec_index.invalidate()
        result[name] = eval_channel(fn, QUESTIONS)
        m = result[name]
        # 分题型看融合是否「两头都顾」：exact 靠词法，paraphrase 靠语义
        detail = ""
        if name in ("vector", "hybrid", "fts"):
            parts = []
            for kind in ("paraphrase", "exact"):
                qs = [q for q in QUESTIONS if q[2] == kind]
                sub = eval_channel(fn, qs)
                parts.append(f"{kind} hit@8={sub['hit8']:.0%} mrr={sub['mrr']:.3f}")
            detail = "  |  " + "  ".join(parts)
        print(f"[base] {name:8s}: hit@8={m['hit8']:.0%} mrr={m['mrr']:.3f} "
              f"lat={m['lat_ms']}ms{detail}")

    if not no_scale:
        result["scale"] = scale_benchmark(scale)
        s = result["scale"]
        print(f"[scale] N={s['n']}  全量重载={s['reload_ms']}ms  "
              f"向量查询(float32)={s['vec_ms']}ms  "
              f"关键词 LIKE={s['keyword_ms']}ms")

    if llm.chat_available():
        # 干扰文档让 hit@8 有区分度（12 篇语料随机基线 67%，130+ 篇 6%）
        t0 = time.perf_counter()
        seed_fillers(client)
        print(f"[followup] 干扰语料就绪（{time.perf_counter()-t0:.1f}s），hit@8 随机基线 "
              f"≈ {8 / (len(DOCS) + 120):.0%}")
        fu = eval_followup()
        result["followup"] = fu
        print(f"[followup] 追问 {fu['n']} 题 · 改写成功 {fu['rewritten']} / 回退 {fu['fallback']}")
        print(f"[followup] 原词直查: hit@8={fu['raw']['hit8']:.0%} mrr={fu['raw']['mrr']:.3f}")
        print(f"[followup] 改写后:   hit@8={fu['rewrite']['hit8']:.0%} mrr={fu['rewrite']['mrr']:.3f}")
    else:
        print("[followup] 对话模型不可用，追问评测跳过")

    print("BASELINE_JSON: " + json.dumps(result, ensure_ascii=False))
    import shutil
    shutil.rmtree(_SANDBOX, ignore_errors=True)


if __name__ == "__main__":
    main()
