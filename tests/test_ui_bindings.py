"""前端绑定守卫：模板里的事件处理函数 / ref / v-model 变量必须在 <script> 里定义。

设置页「配置导出 / 导入」两个按钮曾经只有模板、没有函数（点了毫无反应），
后端方法因此长期无人调用。这类断线让 CI 拦住，不靠人点。

只查三类真会断线的绑定：
- @事件="fn"          → fn 要有定义（内联箭头函数/表达式跳过）
- v-model="x"         → x 要有定义
- ref="x"             → x 要有定义（本项目约定 ref 名与变量名一致）
:prop="x" 不查：模板里大量 v-for 局部变量与作用域变量，误报率高、收益低。
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "frontend", "src")

# 模板里合法但不来自 script 的标识符
_BUILTIN = {"$event", "$refs", "$props", "$emit", "$slots", "$attrs",
            "true", "false", "null", "undefined"}

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S)
_EVENT = re.compile(r'@[\w:.-]+="([^"]*)"')
_VMODEL = re.compile(r'v-model(?::[\w-]+)?="([^"]*)"')
_REF = re.compile(r'\bref="([^"]*)"')
_VFOR = re.compile(r'v-for="([^"]*)"')

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _script_locals(tpl):
    """v-for 引入的局部变量（item / (v, k) / {a, b}）。"""
    names = set()
    for expr in _VFOR.findall(tpl):
        head = re.split(r"\s+(?:in|of)\s+", expr, maxsplit=1)[0]
        names |= set(_IDENT.findall(head))
    return names


def _bound_names(tpl):
    """模板里要求 script 提供定义的标识符。"""
    names = set()
    for expr in _EVENT.findall(tpl):
        e = expr.strip()
        # 内联语句（x = 1）、箭头函数、立即调用一律跳过：它们不依赖顶层同名定义
        if not e or e.startswith("(") or "=>" in e or "=" in e.split("(")[0]:
            continue
        m = _IDENT.match(e)
        if m:
            names.add(m.group(0))
    for expr in _VMODEL.findall(tpl):
        m = _IDENT.match(expr.strip())
        if m:
            names.add(m.group(0))
    for expr in _REF.findall(tpl):
        if expr.strip():
            names.add(expr.strip())
    return names


def check_file(path):
    text = open(path, encoding="utf-8").read()
    m = _SCRIPT.search(text)
    script = m.group(1) if m else ""
    tpl = text[m.end():] if m else text
    defined = set(_IDENT.findall(script))
    missing = sorted(n for n in _bound_names(tpl)
                     if n not in _BUILTIN and n not in defined
                     and n not in _script_locals(tpl))
    try:
        rel = os.path.relpath(path, _ROOT)
    except ValueError:      # 传入仓库外/另一盘符的文件（自检用），保留原路径
        rel = path
    check(f"{rel} 绑定均有定义", not missing, f"未定义: {missing}")
    return missing


def main():
    files = []
    for root, dirs, names in os.walk(_SRC):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist")]
        files += [os.path.join(root, n) for n in names if n.endswith(".vue")]
    files.sort()
    check("扫描到 vue 组件", len(files) > 0, f"{len(files)} 个")

    missing_all = []
    for p in files:
        missing_all += check_file(p)

    # 反向自检：注入一段已知会断线的模板，确认检查器真能抓到
    probe = '<template><n-button @click="fbGhostHandler">x</n-button></template>'
    tpl = probe
    check("检查器能抓到断线按钮",
          "fbGhostHandler" in _bound_names(tpl), str(_bound_names(tpl)))

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)} ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
