"""智能分类：规则判不了的模糊文件交给本地大模型归类（批量请求，10 个/次）。"""
import json
import re

from backend import db
from backend.core import rules as rules_mod
from backend.core import scanner
from backend import llm

BATCH_SIZE = 10

BATCH_PROMPT = """你是文件整理助手。判断下列每个文件属于哪个类别。

可选类别：文档、图片、视频、音频、压缩包、安装包、代码、数据、字体、其他

文件列表：
{items}

注意：exe/msi 是安装包；zip/rar/7z 是压缩包；文件头为 PK 的 docx/pptx/xlsx 属于文档。
只输出一个 JSON 数组，每个元素：{{"name": "文件名", "category": "类别", "reason": "一句话理由"}}，顺序与输入一致。"""

SINGLE_PROMPT = """你是文件整理助手。根据文件信息判断它属于哪个类别。

可选类别：文档、图片、视频、音频、压缩包、安装包、代码、数据、字体、其他

文件名：{name}
扩展名：{ext}
文件头魔数：{magic}
内容摘要：{peek}

只输出一个 JSON 对象：{{"category": "类别", "reason": "一句话理由"}}"""


def is_ambiguous(fi, rules=None):
    """规则无法归类，或落入「其他」的文件，交给 AI 判断。"""
    hit = rules_mod.classify_by_ext(fi.get("ext"), rules)
    return hit is None or hit[0] == "其他"


def _item_line(fi):
    peek, magic = scanner.peek_file(fi["path"], fi.get("ext", ""))
    ext = fi.get("ext") or "无"
    magic = magic or "未知"
    peek = (peek or "").replace("\n", " ")[:150] or "无"
    return f'- 文件名: {fi["name"][:120]} | 扩展名: {ext} | 魔数: {magic} | 摘要: {peek}'


def _parse_batch(answer):
    """解析模型返回的 JSON 数组，容错：截取首个 [...] 段。"""
    m = re.search(r"\[.*\]", answer, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def _parse_single(answer):
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _valid_cat(cat):
    return cat if cat in rules_mod.CATEGORIES else "其他"


def ai_classify(files, client=None, progress_cb=None):
    """批量 AI 分类。files 为扫描结果子集，返回 {path: (category, reason)}。
    批量请求失败时自动降级为逐个请求。"""
    client = client or llm.get_chat_client()
    rules = rules_mod.load_user_rules()
    results = {}
    cache = _load_cache()

    pending = []
    for fi in files:
        cached = cache.get(_cache_key(fi))
        if cached:
            results[fi["path"]] = (cached[0], cached[1])
        else:
            pending.append(fi)

    total = len(pending)
    done = 0
    i = 0
    while i < len(pending):
        batch = pending[i:i + BATCH_SIZE]
        i += BATCH_SIZE
        ok = False
        try:
            answer = client.chat(
                [{"role": "user", "content": BATCH_PROMPT.format(
                    items="\n".join(_item_line(f) for f in batch))}],
                options={"temperature": 0.1, "num_predict": 60 * len(batch)},
                think=False,
            )
            arr = _parse_batch(answer)
            if arr:
                by_name = {}
                for obj in arr:
                    if isinstance(obj, dict) and obj.get("name"):
                        by_name.setdefault(str(obj["name"]), obj)
                for f in batch:
                    obj = by_name.get(f["name"])
                    if obj:
                        results[f["path"]] = (_valid_cat(obj.get("category")),
                                              obj.get("reason", ""))
                        ok = True
        except Exception:
            ok = False
        if not ok:
            # 批量失败 → 逐个兜底
            for f in batch:
                if progress_cb:
                    progress_cb(done + 1, total, f["name"])
                results[f["path"]] = _classify_one(client, f)
                done += 1
        else:
            for f in batch:
                if f["path"] not in results:
                    results[f["path"]] = _classify_one(client, f)
        done += len(batch)
        if progress_cb:
            progress_cb(min(done, total), total, batch[-1]["name"])

    _save_cache(results, files)
    return results


def _classify_one(client, fi):
    peek, magic = scanner.peek_file(fi["path"], fi.get("ext", ""))
    try:
        answer = client.chat(
            [{"role": "user", "content": SINGLE_PROMPT.format(
                name=fi["name"][:120], ext=fi.get("ext") or "无",
                magic=magic or "未知", peek=(peek or "").replace("\n", " ")[:200] or "无")}],
            options={"temperature": 0.1, "num_predict": 200},
            think=False,
        )
        obj = _parse_single(answer)
        return (_valid_cat(obj.get("category")), obj.get("reason", ""))
    except Exception as e:
        return ("其他", f"AI 分类失败：{e}")


def _cache_key(fi):
    return f"{fi['name']}|{fi['size']}"


def _load_cache():
    raw = db.get_setting("ai_classify_cache", "{}")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_cache(new_results, files):
    cache = _load_cache()
    for fi in files:
        r = new_results.get(fi["path"])
        # 失败/空结果不缓存，下次还能重试
        if r and r[1] and not r[1].startswith("AI 分类失败"):
            cache[_cache_key(fi)] = [r[0], r[1]]
    # 缓存上限，避免无限膨胀
    if len(cache) > 20000:
        cache = dict(list(cache.items())[-10000:])
    db.set_setting("ai_classify_cache", json.dumps(cache, ensure_ascii=False))
