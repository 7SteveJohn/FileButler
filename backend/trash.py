"""文件删除到系统回收站（不直接删除，安全可恢复）。"""
import ctypes
import os
from ctypes import wintypes


def send_to_recycle_bin(path):
    """把文件/文件夹送入 Windows 回收站；失败抛 OSError。"""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", ctypes.c_uint),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_bool),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40      # 送回收站（可撤销）
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400

    path = os.path.abspath(path)
    buf = ctypes.create_unicode_buffer(path + "\0")  # pFrom 需双 NULL 结尾
    op = SHFILEOPSTRUCTW(0, FO_DELETE, ctypes.cast(buf, wintypes.LPCWSTR), None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                         False, None, None)
    ret = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if ret != 0:
        raise OSError(f"回收站操作失败（错误码 {ret}）")
    if op.fAnyOperationsAborted:
        raise OSError("操作被系统中止")
