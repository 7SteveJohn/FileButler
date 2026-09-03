"""FileButler 入口：--dev 连接 Vite 开发服务器，默认加载打包内前端。
关闭窗口 = 最小化到托盘；托盘菜单可显示/退出。"""
import os
import sys
import threading
import time

# 无控制台的打包模式下 stdout/stderr 为 None，需先判空
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _resource(rel):
    """资源路径：打包后在 _MEIPASS，开发时在项目目录。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def _make_tray_icon():
    """用户设计的应用图标；缺失时退回程序绘制的简易图标。"""
    from PIL import Image, ImageDraw
    icon_png = _resource(os.path.join("icon", "file-butler-tray.png"))
    try:
        return Image.open(icon_png)
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(24, 160, 88))
        d.rectangle([22, 16, 30, 48], fill="white")
        d.rectangle([22, 16, 44, 24], fill="white")
        d.rectangle([22, 30, 38, 38], fill="white")
        return img


def _version():
    try:
        from backend import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _handle_sendto_args():
    """右键「发送到 FileButler」传入的文件路径 → 直接入库（不弹错误）。"""
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if not args:
        return
    try:
        from backend import fileindex
        for f in args:
            try:
                fileindex.upsert_file(f)
            except Exception:
                pass
    except Exception:
        pass


def _ensure_sendto_shortcut():
    """在 SendTo 目录创建快捷方式，实现资源管理器右键「发送到 FileButler」。"""
    try:
        import subprocess
        sendto = os.path.join(os.environ.get("APPDATA", ""),
                              "Microsoft", "Windows", "SendTo")
        if not sendto or not os.path.isdir(sendto):
            return
        exe = sys.executable if getattr(sys, "frozen", False) else \
            os.path.abspath(sys.argv[0])
        lnk = os.path.join(sendto, "FileButler.lnk")
        if os.path.exists(lnk):
            return  # 已安装
        ps = ("$ws = New-Object -ComObject WScript.Shell;"
              "$s = $ws.CreateShortcut('" + lnk + "');"
              f"$s.TargetPath = '{exe}';"
              f"$s.WorkingDirectory = '{os.path.dirname(exe)}';"
              "$s.Description = '发送到 FileButler 本地文件管家';"
              "$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=0x08000000, timeout=20, check=False)
    except Exception:
        pass


# ---------- 单实例锁 ----------
# SendTo / 右键集成 每次都会启动新进程，必须用 socket 锁保证只有一个实例运行。
# 第一个进程 bind 成功，监听后续进程发来的 SendTo/右键路径；
# 后续进程 bind 失败，把 argv 路径发给已有实例后立即退出。
_SINGLE_PORT = 47928
_single_sock = None
_exiting = threading.Event()  # 退出信号：weekly_rescan 等后台循环据此收尾


def _try_acquire_single_instance():
    """尝试 bind 端口成为唯一实例。返回 True 表示成功，False 表示已有实例运行。
    注意：不能设 SO_REUSEADDR——Windows 上它允许跨进程 bind 同一端口，
    会让单实例锁失效。Windows 默认（独占 bind）即可保证单实例。"""
    import socket as _socket
    global _single_sock
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    # 故意不调 setsockopt(SO_REUSEADDR, 1)——见上方注释
    try:
        s.bind(('127.0.0.1', _SINGLE_PORT))
        s.listen(8)
        _single_sock = s
        return True
    except OSError:
        s.close()
        # 已有实例：把 argv 路径（SendTo/右键传入）发过去，然后退出
        try:
            c = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            c.settimeout(2)
            c.connect(('127.0.0.1', _SINGLE_PORT))
            for p in [a for a in sys.argv[1:] if os.path.exists(a)]:
                c.sendall(p.encode('utf-8') + b'\n')
            c.close()
        except Exception:
            pass
        return False


def _start_single_server(window, api):
    """后台线程：接收后续进程的 SendTo/右键路径，入库并把已有窗口拉到前台。"""
    import threading as _threading
    from backend import fileindex as _fi
    if _single_sock is None:
        return
    def loop():
        while True:
            try:
                c, _ = _single_sock.accept()
            except Exception:
                continue
            try:
                data = b''
                c.settimeout(3)
                while True:
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b'\n' in chunk:
                        break
                c.close()
            except Exception:
                try: c.close()
                except Exception: pass
                continue
            try:
                paths = [ln.decode('utf-8', errors='ignore').strip()
                         for ln in data.splitlines() if ln.strip()]
                added = 0
                for p in paths:
                    if os.path.exists(p):
                        try:
                            _fi.upsert_file(p)
                            added += 1
                        except Exception:
                            pass
                if added:
                    try:
                        api.set_visible(True)
                        window.show()
                    except Exception: pass
                    # 通过 evaluate_js 触发前端：切到 Files 页 + 弹"已加入"消息
                    try:
                        import json as _json
                        names = [os.path.basename(p) for p in paths if os.path.exists(p)]
                        window.evaluate_js(
                            'window.__fbSendToIngest && window.__fbSendToIngest('
                            + _json.dumps(names) + ')')
                    except Exception:
                        pass
            except Exception:
                pass
    _threading.Thread(target=loop, daemon=True, name="FB-SendToServer").start()


def _ensure_context_menu():
    """注册资源管理器右键菜单（HKCU，无需管理员）：
    文件右键 → 用 FileButler 打开；文件夹右键 → 用 FileButler 浏览。
    传入路径由 _handle_sendto_args 接收（入库），并正常启动窗口。"""
    try:
        import winreg
        exe = sys.executable if getattr(sys, "frozen", False) else \
            os.path.abspath(sys.argv[0])
        # 文件：用 FileButler 打开
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\*\shell\FileButlerOpen") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, "用 FileButler 打开")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\*\shell\FileButlerOpen\command") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, f'"{exe}" "%1"')
        # 文件夹：用 FileButler 浏览
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\Directory\shell\FileButlerBrowse") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, "用 FileButler 浏览")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\Directory\shell\FileButlerBrowse\command") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, f'"{exe}" "%1"')
    except Exception:
        pass


def main():
    import webview
    from backend.api import Api

    # 单实例锁：SendTo/右键触发的二次进程把 argv 路径发给已有实例后退出
    # 必须在 _handle_sendto_args 之前调用（避免无意义地重复入库）
    if not _try_acquire_single_instance():
        sys.exit(0)

    _handle_sendto_args()

    dev = "--dev" in sys.argv
    api = Api()

    # 资源管理器集成（SendTo 快捷方式要 spawn powershell，可能秒级）
    # 移到后台线程：不能让它挡在窗口创建前面拖慢首屏
    threading.Thread(target=lambda: (_ensure_sendto_shortcut(),
                                     _ensure_context_menu()),
                     daemon=True, name="fb-shell-integration").start()
    if dev:
        url = "http://localhost:5173"
    else:
        dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist", "index.html")
        url = dist if os.path.exists(dist) else "http://localhost:5173"

    window = webview.create_window(
        f"FileButler v{_version()} · 本地智能文件管家",
        url,
        js_api=api,
        width=1280,
        height=840,
        min_size=(980, 660),
    )
    api.set_window(window)
    _start_single_server(window, api)  # 监听后续进程的 SendTo/右键路径

    # ---------- 全局热键（pynput，后台线程） ----------

    def _start_hotkey():
        try:
            import pynput.keyboard as kb
        except ImportError:
            return

        def _on_trigger():
            try:
                api.set_visible(True)
                window.show()
                window.evaluate_js(
                    'window.__fbEvent && window.__fbEvent('
                    '{"name":"hotkey_trigger","payload":{}})')
            except Exception:
                pass

        def _run():
            """持续运行，每 5 秒检查设置变化并重建监听器（触发本身即时，
            轮询只影响设置变更的生效延迟，无需 1s 高频）。"""
            def _norm_combo(c):
                """pynput 用 cmd 表示 Win 键，把用户输入的 Win 规范化。"""
                parts = []
                for p in c.replace(" ", "").split("+"):
                    p = p.strip()
                    parts.append("cmd" if p.lower() == "win" else p)
                return "+".join(parts)

            listener = None
            last_combo = None
            while True:
                try:
                    from backend import db as _db
                    enabled = _db.get_setting("hotkey_enabled", "0") == "1"
                    combo = _norm_combo(_db.get_setting("hotkey_combo", "Alt+Space"))
                except Exception:
                    enabled, combo = False, "Alt+Space"

                if not enabled or not combo:
                    if listener:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                        listener = None
                    time.sleep(5)
                    continue

                if combo != last_combo or listener is None:
                    if listener:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                    try:
                        hotkey_map = {combo: _on_trigger}
                        listener = kb.GlobalHotKeys(hotkey_map)
                        listener.start()
                        last_combo = combo
                    except Exception:
                        listener = None
                        time.sleep(5)
                        continue

                time.sleep(5)

        threading.Thread(target=_run, daemon=True, name="fb-hotkey").start()

    _start_hotkey()

    def on_loaded():
        # 窗口就绪后再启动后台服务：全量索引 + 实时监控 + 缩略图服务
        try:
            api.start_services()
        except Exception as e:
            print("start_services error:", e)
        # 每周兜底全量重扫（防止 watchdog 丢事件 / 应用未运行期间的删改）
        def weekly_rescan():
            from backend import db as _db
            while not _exiting.is_set():
                # pywebview 6.x 的 Window 没有 closing 属性（旧代码启动即崩），改用退出事件
                _exiting.wait(3600)  # 每小时醒一次检查
                if _exiting.is_set():
                    return
                try:
                    last_scan = float(_db.get_setting("last_full_scan_at", "0") or "0")
                    if time.time() - last_scan >= 7 * 86400:
                        api.set_visible(True)  # 扫描事件需要能推给前端
                        api.rescan_library()
                except Exception:
                    pass

        threading.Thread(target=weekly_rescan, daemon=True, name="fb-weekly-rescan").start()

    window.events.loaded += on_loaded

    # ---- 托盘 ----
    tray = {}

    def quit_app(icon=None, item=None):
        _exiting.set()
        try:
            # 每日自动备份（距上次备份 >20 小时且库有变更才执行；
            # 未变更时跳过 920MB 级的无谓复制）
            from backend import db
            import time as _t
            last = float(db.get_setting("last_backup_at", "0") or 0)
            if _t.time() - last > 20 * 3600:
                db.backup_db(only_if_changed=True)
                db.set_setting("last_backup_at", str(_t.time()))
        except Exception:
            pass
        try:
            if tray.get("icon"):
                tray["icon"].stop()
        except Exception:
            pass
        try:
            if api.watch_engine:
                api.watch_engine.stop()
            if api.thumb.server:
                api.thumb.server.stop()
        except Exception:
            pass
        # 关键：显式关闭单实例锁 socket，否则端口在 TIME_WAIT 期间不能再次 bind，
        # 用户从托盘退出后再启动应用会因 bind 失败而走"二次进程"分支静默退出
        try:
            if _single_sock is not None:
                _single_sock.close()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        # 强制退出：window.destroy 不会让 webview.start() 主线程立即返回
        # 进程残留导致用户"从托盘退出"无效，必须 os._exit 收尾
        os._exit(0)

    def show_window(icon=None, item=None):
        api.set_visible(True)
        window.show()

    def on_closing():
        # 点 X = 隐藏到托盘，不退出
        try:
            import pystray
            if tray.get("icon") is None:
                from pystray import Icon, Menu, MenuItem
                icon = Icon(
                    "FileButler", _make_tray_icon(), "FileButler · 本地智能文件管家",
                    menu=Menu(
                        MenuItem("打开主窗口", show_window, default=True),
                        MenuItem("退出", quit_app),
                    ),
                )
                tray["icon"] = icon
                threading.Thread(target=icon.run, daemon=True).start()
            api.set_visible(False)  # 隐藏期间后端不再向前端推 JS 事件
            window.hide()
        except Exception:
            return True  # 托盘不可用时正常关闭
        return False  # 阻止销毁，仅隐藏

    window.events.closing += on_closing

    # 命令行 --quit-隐式：无
    try:
        webview.start(debug=dev)
    finally:
        # 兜底关闭单实例锁 socket（quit_app 已主动关，X 关闭窗口等路径走这里）
        try:
            if _single_sock is not None:
                _single_sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
