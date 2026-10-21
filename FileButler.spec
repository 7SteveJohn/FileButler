# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('frontend/dist', 'frontend/dist'), ('icon', 'icon')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# winrt（Windows 内置 OCR）为惰性导入，PyInstaller 静态分析收集不到，需显式收集；
# pypinyin（拼音检索，数据随包）/ onnxruntime + tokenizers（rerank 精排，惰性导入）同理
for _pkg in ('winrt.runtime',
             'winrt.windows.media.ocr',
             'winrt.windows.graphics.imaging',
             'winrt.windows.globalization',
             'winrt.windows.storage.streams',
             'winrt.windows.security.cryptography',
             'winrt.windows.foundation',
             'winrt.windows.foundation.collections',
             'pypinyin',
             'onnxruntime',
             'tokenizers'):
    try:
        tmp_ret = collect_all(_pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        pass


def _is_winrt_msvcp(entry):
    """winrt 包自带一份旧版 MSVCP140.dll，collect_all 会把它收到
    _internal/winrt/ 下，和 PyInstaller 从 Python 环境收集到 _internal/
    根的新版并存。winrt 的 .pyd 会优先加载同目录那份旧版，与
    onnxruntime / tokenizers 用的运行库冲突，表现为启动瞬间
    0xc0000005（v1.3.0 实测闪退，事件日志指向 winrt\\MSVCP140.dll）。
    这里只保留根目录那份。"""
    for part in entry[:2]:
        if isinstance(part, str) and part.replace("\\", "/").lower().endswith(
                "winrt/msvcp140.dll"):
            return True
    return False


binaries = [b for b in binaries if not _is_winrt_msvcp(b)]
datas = [d for d in datas if not _is_winrt_msvcp(d)]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 依赖分析会把 winrt 的 .pyd 依赖的 MSVCP140.dll 复制到同目录
# （_internal/winrt/），这一步发生在 Analysis 内部，所以必须在
# a.binaries 上再过滤一次。TOC 元素是 (dest, src, typecode)。
a.binaries = [b for b in a.binaries if not _is_winrt_msvcp(b)]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FileButler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon/FileButler.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FileButler',
)
