#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FileButler 发版脚本：一条命令同步全部版本号，终结"发版漏改 README"。

用法（在仓库根执行，也支持任意目录——仓库根按脚本自身位置推导）：

    python scripts/release.py --version 1.5.0            # 同步四处并打印每处前后行
    python scripts/release.py --version 1.5.0 --check    # 只定位不写文件；有缺失则退出码 1
    python scripts/release.py --version 1.5.0 --build    # 同步后调 ISCC 出包并打印产物 SHA-256

同步的四处（对应 docs/internal/HANDOFF.md §3「发版时版本号要同步四处」）：

    1. backend/__init__.py 的 __version__
    2. FileButler.iss 的 MyAppVersion（OutputBaseFilename 由它派生，脚本会顺带校验）
    3. README.md 下载指引里的 FileButler-Setup-<版本>.exe
    4. README.zh-CN.md 下载指引里的 FileButler-Setup-<版本>.exe

实现要点：

    - 源版本从 backend/__init__.py 读出（唯一事实来源）；其余锚点的当前值必须等于
      源版本（待改）或目标版本（已是新版则跳过），否则视为漂移、拒绝替换——
      正是防 README 停在旧版本号（1.3.1 之后连漏两次发版那种事故）。
    - 每个锚点要求恰好命中 1 处；先全部定位校验，再统一写盘，
      任一处异常则一处都不写（避免半同步状态）。
    - 文件按字节读写，显式 UTF-8 编解码，探测并保留 BOM；
      除版本号子串外不碰任何字节，换行符原样保留。
    - 只用 Python 3.10+ 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BOM = b"\xef\xbb\xbf"
# 版本号形态：三段纯数字（如 1.4.2）。不接受其它形态，锚点正则按此设计。
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# 各文件的锚点；捕获组 1 恒为该处当前版本号
RE_PY_VERSION = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"\s*$', re.M)
RE_ISS_VERSION = re.compile(r'^#define MyAppVersion\s+"(\d+\.\d+\.\d+)"\s*$', re.M)
RE_README_EXE = re.compile(r"FileButler-Setup-(\d+\.\d+\.\d+)\.exe")
# 校验 OutputBaseFilename 是否引用 MyAppVersion（引用了才说明输出文件名随版本派生）
RE_OUTPUT_BASE = re.compile(r"^OutputBaseFilename\s*=.*\{#MyAppVersion\}", re.M)

# ISCC 候选路径，按优先级：per-user 安装（实测所在，见 docs/internal/HANDOFF.md §3）→ 系统级安装
ISCC_CANDIDATES = (
    ("LOCALAPPDATA", "Programs", "Inno Setup 6", "ISCC.exe"),
    ("ProgramFiles", "Inno Setup 6", "ISCC.exe"),
    ("ProgramFiles(x86)", "Inno Setup 6", "ISCC.exe"),
)

# 各处状态
ST_REPLACE = "REPLACE"    # 找到且等于源版本，待替换
ST_NOOP = "NOOP"          # 已是目标版本，跳过
ST_MISS = "MISS"          # 文件或锚点未找到
ST_AMBIG = "AMBIG"        # 锚点命中多处，拒绝处理
ST_MISMATCH = "MISMATCH"  # 值既不是源版本也不是目标版本（疑似漂移）
ST_ERR = "ERR"            # 文件读取/解码失败


class Fatal(Exception):
    """无法继续执行时抛出，main 里统一转成退出码 1。"""


@dataclass
class Target:
    rel_path: Path      # 相对仓库根
    regex: re.Pattern   # 捕获组 1 = 当前版本号
    label: str          # 展示名


@dataclass
class Located:
    target: Target
    status: str
    msg: str
    text: str | None = None
    has_bom: bool = False
    match: re.Match | None = None


TARGETS = [
    Target(Path("backend/__init__.py"), RE_PY_VERSION, "__version__"),
    Target(Path("FileButler.iss"), RE_ISS_VERSION, "MyAppVersion"),
    Target(Path("README.md"), RE_README_EXE, "README 下载文件名"),
    Target(Path("README.zh-CN.md"), RE_README_EXE, "README.zh-CN 下载文件名"),
]


def decode_best(data: bytes) -> str:
    """子进程输出解码：先 UTF-8，再 GBK（ISCC/git 的 ANSI 输出），最后兜底替换坏字节。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_text(path: Path) -> tuple[str, bool]:
    """按字节读文件再显式按 UTF-8 解码；返回 (文本, 是否带 BOM)。"""
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM)
    return raw.decode("utf-8-sig" if has_bom else "utf-8"), has_bom


def save_text(path: Path, text: str, has_bom: bool) -> None:
    """显式按 UTF-8 写回；原文件有 BOM 则原样带回去。"""
    data = text.encode("utf-8")
    if has_bom:
        data = BOM + data
    path.write_bytes(data)


def line_context(text: str, pos: int) -> tuple[int, str, int]:
    """返回 pos 所在的行号（1 起）、该行内容（不含行尾换行）、行首在全文的偏移。"""
    line_no = text.count("\n", 0, pos) + 1
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return line_no, text[start:end].rstrip("\r"), start


def replace_match(match: re.Match, new: str) -> str:
    """把匹配片段里捕获组 1 换成新版本号，片段其余部分一字节不动。"""
    s = match.group(0)
    a = match.start(1) - match.start(0)
    b = match.end(1) - match.start(0)
    return s[:a] + new + s[b:]


def warn_dirty_worktree() -> None:
    """工作树不干净时打印警告但继续（发版前建议先提交，是否提交由人决定）。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True
        )
    except OSError:
        print("提示：git 不可用，跳过工作树状态检查")
        return
    if proc.returncode != 0:
        print(f"提示：git status 失败（退出码 {proc.returncode}），跳过工作树状态检查")
        return
    lines = [ln for ln in decode_best(proc.stdout).splitlines() if ln.strip()]
    if lines:
        print(f"警告：工作树不干净（{len(lines)} 处改动），发版前建议先提交；按约定继续执行：")
        for ln in lines[:20]:
            print(f"    {ln}")
        if len(lines) > 20:
            print(f"    …（还有 {len(lines) - 20} 处）")
    print()


def discover_old() -> str:
    """从 backend/__init__.py 读出当前源版本；拿不到就直接终止，绝不猜。"""
    path = REPO_ROOT / "backend" / "__init__.py"
    if not path.is_file():
        raise Fatal(f"找不到 {path.as_posix()}，无法确定当前源版本")
    try:
        text, _ = load_text(path)
    except UnicodeDecodeError as exc:
        raise Fatal(f"{path.as_posix()} 不是合法 UTF-8，无法解析：{exc}")
    matches = list(RE_PY_VERSION.finditer(text))
    if len(matches) != 1:
        raise Fatal(
            f"{path.as_posix()} 里 __version__ 锚点命中 {len(matches)} 处（应为恰好 1 处），"
            "无法确定源版本，请人工确认"
        )
    return matches[0].group(1)


def locate(target: Target, old: str, new: str) -> Located:
    """在单个目标文件里定位锚点并校验当前值。"""
    path = REPO_ROOT / target.rel_path
    if not path.is_file():
        return Located(target, ST_MISS, "文件不存在")
    try:
        text, has_bom = load_text(path)
    except UnicodeDecodeError as exc:
        return Located(target, ST_ERR, f"不是合法 UTF-8，拒绝处理：{exc}")
    matches = list(target.regex.finditer(text))
    if not matches:
        return Located(
            target, ST_MISS, "未找到版本锚点（文件形态可能变了，请人工确认）", text, has_bom
        )
    if len(matches) > 1:
        return Located(
            target, ST_AMBIG, f"锚点命中 {len(matches)} 处，为避免误改拒绝处理", text, has_bom
        )
    match = matches[0]
    cur = match.group(1)
    if cur == new:
        return Located(target, ST_NOOP, f"已是目标版本 {new}", text, has_bom, match)
    if cur != old:
        return Located(
            target,
            ST_MISMATCH,
            f"当前值 {cur}，既不等于源版本 {old} 也未更新到目标版本 {new}（疑似漂移）",
            text,
            has_bom,
            match,
        )
    return Located(target, ST_REPLACE, "", text, has_bom, match)


def note_output_base(iss_text: str | None) -> str | None:
    """校验 OutputBaseFilename 是否由 MyAppVersion 派生，返回一句提示或 None。"""
    if iss_text is None:
        return None
    if RE_OUTPUT_BASE.search(iss_text):
        return "已确认：OutputBaseFilename 引用 {#MyAppVersion}，输出文件名随版本派生，无需单独改"
    return "警告：OutputBaseFilename 未引用 {#MyAppVersion}，输出文件名不会随版本变化，请人工确认"


def find_iscc() -> tuple[Path | None, list[str]]:
    """按候选清单找 ISCC.exe，返回 (命中路径或 None, 已查过的路径列表)。"""
    searched: list[str] = []
    for env_key, *parts in ISCC_CANDIDATES:
        base = os.environ.get(env_key)
        if not base:
            searched.append(f"%{env_key}% 未设置")
            continue
        cand = Path(base).joinpath(*parts)
        searched.append(str(cand))
        if cand.is_file():
            return cand, searched
    return None, searched


def sha256_file(path: Path) -> str:
    """分块计算文件 SHA-256（安装包几十 MB，不整读进内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_build(new: str) -> int:
    """调 ISCC 编译 FileButler.iss，成功后打印产物路径与 SHA-256。"""
    iscc, searched = find_iscc()
    if iscc is None:
        print("错误：找不到 ISCC.exe，已查过以下位置：")
        for s in searched:
            print(f"    {s}")
        print("提示：Inno Setup 6 常为 per-user 安装，在 %LOCALAPPDATA%\\Programs\\Inno Setup 6\\ 下")
        return 1
    print(f"调用 ISCC：{iscc}")
    print("提醒：.iss 的前置步骤是 npm run build + PyInstaller；本脚本只做版本同步与 ISCC 编译。")
    proc = subprocess.run([str(iscc), "FileButler.iss"], cwd=str(REPO_ROOT), capture_output=True)
    out_lines = decode_best(proc.stdout).strip().splitlines()
    tail = "\n".join(f"    {ln}" for ln in out_lines[-8:]) if out_lines else "    （ISCC 无输出）"
    if proc.returncode != 0:
        print(f"ISCC 编译失败（退出码 {proc.returncode}），输出末尾：")
        print(tail)
        err = decode_best(proc.stderr).strip()
        if err:
            print(f"stderr：{err}")
        return 1
    print("ISCC 输出末尾：")
    print(tail)
    exe = REPO_ROOT / "installer" / f"FileButler-Setup-{new}.exe"
    if not exe.is_file():
        print(f"错误：ISCC 退出码为 0，但没找到产物 {exe.as_posix()}，请人工确认")
        return 1
    size = exe.stat().st_size
    digest = sha256_file(exe)
    print(f"产物：installer\\FileButler-Setup-{new}.exe（{size:,} 字节）")
    print(f"SHA-256：{digest}")
    print("（把上面 SHA-256 一行复制贴到 GitHub Release 说明里即可）")
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="release.py",
        description="FileButler 发版脚本：一条命令同步全部版本号（见 docs/internal/HANDOFF.md §3）。",
    )
    ap.add_argument("--version", required=True, metavar="X.Y.Z",
                    help="目标版本号，三段纯数字（如 1.5.0）")
    ap.add_argument("--check", action="store_true",
                    help="只定位并打印将改动的内容，不写任何文件")
    ap.add_argument("--build", action="store_true",
                    help="同步后调用 ISCC 编译 FileButler.iss，并打印产物 SHA-256")
    return ap.parse_args()


def main() -> int:
    # Windows 下输出被管道/重定向时，Python 默认按本机代码页（GBK）编码，中文会花屏
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.check and args.build:
        print("错误：--check 与 --build 不能同时使用（--check 不写文件，无法构建）")
        return 2
    new = args.version
    if not VERSION_RE.match(new):
        print(f"错误：--version 必须是三段纯数字（如 1.5.0），收到：{new!r}")
        return 2

    warn_dirty_worktree()

    try:
        old = discover_old()
    except Fatal as exc:
        print(f"错误：{exc}")
        return 1
    if old == new:
        print(f"源版本已等于目标版本 {new}，逐处核对是否都已到位：")
    else:
        print(f"FileButler 版本同步：{old} → {new}")
    print()

    results = [locate(target, old, new) for target in TARGETS]

    # 打印每处的定位结果与前后行（--check 模式下的"将改为"行也在这里）
    for idx, res in enumerate(results, 1):
        head = f"[{idx}/{len(results)}] {res.target.rel_path.as_posix()}（{res.target.label}）"
        print(head)
        if res.status in (ST_REPLACE, ST_NOOP):
            line_no, line, line_start = line_context(res.text, res.match.start())
            print(f"  第 {line_no} 行 前: {line}")
            if res.status == ST_REPLACE:
                # 把替换片段拼回整行再打印，"将改为"展示的是完整目标行
                a = res.match.start() - line_start
                b = res.match.end() - line_start
                after = line[:a] + replace_match(res.match, new) + line[b:]
                print(f"          将改为: {after}")
            else:
                print(f"          （无需改动）{res.msg}")
        else:
            print(f"  [!!] {res.msg}")
        print()

    iss_text = next(
        (r.text for r in results if r.target.rel_path.name == "FileButler.iss"), None
    )
    note = note_output_base(iss_text)
    if note:
        print(note)
        print()

    bad = [r for r in results if r.status not in (ST_REPLACE, ST_NOOP)]
    if bad:
        suffix = "（--check 本就不写文件）" if args.check else "，未写入任何文件"
        print(f"以下 {len(bad)} 处未通过校验{suffix}：")
        for r in bad:
            print(f"  [!!] {r.target.rel_path.as_posix()}：{r.msg}")
        return 1

    if args.check:
        print(f"检查通过：{len(results)} 处目标全部定位，未写任何文件。")
        return 0

    # 全部通过才写盘：只写需要改的，已是目标版本的原样不动
    written: list[str] = []
    for res in results:
        if res.status != ST_REPLACE:
            continue
        new_text = (
            res.text[: res.match.start()]
            + replace_match(res.match, new)
            + res.text[res.match.end():]
        )
        save_text(REPO_ROOT / res.target.rel_path, new_text, res.has_bom)
        written.append(res.target.rel_path.as_posix())
        print(f"已写入 {res.target.rel_path.as_posix()}")
    if written:
        print(f"同步完成：{len(written)} 处已写入。")
    else:
        print("所有目标已是该版本，无需改动。")

    if args.build:
        print()
        return run_build(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
