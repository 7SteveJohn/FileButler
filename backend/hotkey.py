"""Windows 全局热键（Win32 RegisterHotKey，零第三方依赖）。

替代原先基于 pynput 的实现：
1. pynput 要求 `<alt>+<space>` 尖括号语法，传 `Alt+Space` 会静默抛 ValueError；
2. pynput 挂全局低级键盘钩子（WH_KEYBOARD_LL），接管系统所有按键消息；
3. 原生 RegisterHotKey 由系统内核分发 WM_HOTKEY，零轮询、零钩子开销，
   且能当场返回热键是否语法错误或已被其他软件（如 PowerToys / uTools）占用。
"""
import ctypes
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MOD_MAP = {
    "alt": MOD_ALT,
    "menu": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

_VK_MAP = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "`": 0xC0,
    "~": 0xC0,
    "tilde": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}
for _i in range(1, 25):
    _VK_MAP[f"f{_i}"] = 0x70 + (_i - 1)


def parse_combo(combo: str):
    """解析热键字符串（如 'Alt+Space'、'Ctrl+Shift+F'、'<alt>+<space>'）。
    成功返回 (modifiers, vk)，非法返回 None。"""
    if not combo or not isinstance(combo, str):
        return None
    parts = [p.strip().strip("<>").lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    vk = None
    for p in parts:
        if p in _MOD_MAP:
            mods |= _MOD_MAP[p]
            continue
        if vk is not None:
            return None  # 不允许两个非修饰主键
        if len(p) == 1 and "a" <= p <= "z":
            vk = ord(p.upper())
        elif len(p) == 1 and "0" <= p <= "9":
            vk = ord(p)
        elif p in _VK_MAP:
            vk = _VK_MAP[p]
        else:
            return None
    if vk is None:
        return None
    # 普通字母/数字/空格必须配合至少一个修饰键，避免劫持正常打字；F1-F24 允许单键
    is_fkey = 0x70 <= vk <= 0x87
    if mods == 0 and not is_fkey:
        return None
    return mods, vk


class HotkeyListener:
    """单组合键后台监听线程（Win32 GetMessageW 阻塞等待，0 CPU 占用）。"""

    def __init__(self, combo: str, on_trigger):
        self.combo = combo
        self._on_trigger = on_trigger
        self._tid = None
        self._thread = None
        self.ok = False
        self.error = ""

    def start(self):
        parsed = parse_combo(self.combo)
        if parsed is None:
            self.ok = False
            self.error = f"无效的组合键格式：{self.combo}"
            return False
        mods, vk = parsed
        ready = threading.Event()

        def _loop():
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._tid = kernel32.GetCurrentThreadId()
            # 强制创建线程消息队列，避免 PostThreadMessageW 在 GetMessageW 前丢失
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
                self.ok = False
                self.error = f"热键 {self.combo} 注册失败（可能已被其他程序占用）"
                ready.set()
                return
            self.ok = True
            ready.set()
            try:
                while True:
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:  # 0 = WM_QUIT, -1 = error
                        break
                    if msg.message == WM_HOTKEY:
                        try:
                            self._on_trigger()
                        except Exception:
                            pass
            finally:
                user32.UnregisterHotKey(None, 1)

        self._thread = threading.Thread(target=_loop, daemon=True, name="fb-hotkey")
        self._thread.start()
        ready.wait(timeout=2.0)
        return self.ok

    def stop(self):
        tid, th = self._tid, self._thread
        self._tid = None
        self._thread = None
        if tid is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=1.5)


class HotkeyController:
    """管理全局热键启停与动态切换（线程安全）。"""

    def __init__(self, on_trigger):
        self._on_trigger = on_trigger
        self._lock = threading.Lock()
        self._listener = None

    def apply(self, enabled: bool, combo: str = "Alt+Space"):
        with self._lock:
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            if not enabled:
                return {"ok": True, "enabled": False, "hotkey": combo}
            lst = HotkeyListener(combo, self._on_trigger)
            if lst.start():
                self._listener = lst
                return {"ok": True, "enabled": True, "hotkey": combo}
            return {"ok": False, "error": lst.error or "热键注册失败"}

    def stop(self):
        with self._lock:
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
