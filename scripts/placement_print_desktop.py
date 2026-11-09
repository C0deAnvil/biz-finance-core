"""Windows desktop launcher for the placement print agent.

Run with ``venv\\Scripts\\pythonw.exe scripts\\placement_print_desktop.py``.
The device token is kept only in memory and passed to the child agent process.
"""

import json
import ctypes
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from pathlib import Path
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.placement_print_config import (  # noqa: E402
    is_autostart_enabled, load_settings, save_settings, set_autostart,
)

SAMPLE_PDF = ROOT / "output" / "pdf" / "sample_placement_order.pdf"
DEFAULT_SERVER_URL = "https://www.cphwchem.xyz:8001"
ADMIN_URL = f"{DEFAULT_SERVER_URL}/admin/print-agents"
SUMATRA_URL = "https://www.sumatrapdfreader.org/download-free-pdf-viewer"
DEFAULT_PRINTER = "EPSONB8696A (L3250 Series)"
LEGACY_SERVER_URLS = {"http://8.140.196.85", "http://8.140.196.85:8001"}


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))


def agent_command(*args):
    """Return the worker command for source and packaged executions."""
    if is_frozen_app():
        return [sys.executable, "--agent", *args]
    return [sys.executable, "-u", "-m", "scripts.placement_print_agent", *args]


def _child_log_path():
    return Path(tempfile.gettempdir()) / f"yecai-print-{os.getpid()}-{uuid.uuid4().hex}.log"


def _read_child_log(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def current_server_url(value):
    """Move previously saved insecure server addresses to the deployed HTTPS endpoint."""
    configured = str(value or "").strip().rstrip("/")
    return DEFAULT_SERVER_URL if configured in LEGACY_SERVER_URLS or not configured else configured


def list_installed_printers():
    """List printers installed in Windows, regardless of their connection type."""
    if sys.platform != "win32":
        return []

    class PrinterInfo4(ctypes.Structure):
        _fields_ = [
            ("printer_name", wintypes.LPWSTR),
            ("server_name", wintypes.LPWSTR),
            ("attributes", wintypes.DWORD),
        ]

    enum = ctypes.WinDLL("winspool.drv", use_last_error=True).EnumPrintersW
    byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
    enum.argtypes = [
        wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD,
        byte_pointer, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ]
    enum.restype = wintypes.BOOL
    needed, returned = wintypes.DWORD(), wintypes.DWORD()
    flags = 0x00000002 | 0x00000004  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    enum(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if not needed.value:
        error = ctypes.get_last_error()
        if error not in (0, 122):  # ERROR_INSUFFICIENT_BUFFER is expected on probe.
            raise OSError(error, "Windows 打印机列表读取失败")
        return []
    buffer = (ctypes.c_ubyte * needed.value)()
    if not enum(flags, None, 4, buffer, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
        raise OSError(ctypes.get_last_error(), "Windows 打印机列表读取失败")
    records = ctypes.cast(buffer, ctypes.POINTER(PrinterInfo4))
    return sorted({records[index].printer_name for index in range(returned.value) if records[index].printer_name})


def filter_printers(names, keyword):
    query = keyword.strip().casefold()
    return [name for name in names if query in name.casefold()]


def find_sumatra():
    bundled_root = Path(getattr(sys, "_MEIPASS", ROOT))
    candidates = [bundled_root / "SumatraPDF" / "SumatraPDF.exe"]
    candidates.extend([
        Path(os.environ[name]) / "SumatraPDF" / "SumatraPDF.exe"
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        if os.environ.get(name)
    ])
    installed = next((path for path in candidates if sumatra_is_printable(path)), None)
    if installed:
        return str(installed)
    found = shutil.which("SumatraPDF.exe")
    return found if found and sumatra_is_printable(Path(found)) else ""


def sumatra_is_printable(path):
    """A copied/renamed installer is not a PDF printer, even when named SumatraPDF.exe."""
    path = Path(path)
    if not path.is_file() or "sumatrapdf" not in path.name.casefold() or "-install" in path.name.casefold():
        return False
    if path.name.casefold() == "sumatrapdf.exe":
        # Installed Sumatra 3.6 ships libmupdf.dll; newer builds use libsumatrapdf.dll.
        # A bare same-named installer copy has neither and launches the installer UI.
        return any((path.parent / library).is_file() for library in ("libmupdf.dll", "libsumatrapdf.dll"))
    return True  # Official versioned portable executable.


def print_command(sumatra_path, printer_name):
    """Sumatra submits the PDF to the Windows-installed printer by name."""
    program_path = Path(sumatra_path)
    if not program_path.is_absolute():
        program_path = ROOT / program_path
    if not program_path.is_file():
        raise ValueError("请选择已安装的 SumatraPDF.exe")
    if not sumatra_is_printable(program_path):
        raise ValueError("所选文件是安装包或缺少配套 DLL，不能执行打印；请选择已安装目录中的 SumatraPDF.exe")
    if not printer_name.strip():
        raise ValueError("请填写 Windows 打印机名称")
    return [
        str(program_path.resolve()),
        "-print-to", printer_name.strip(),
        "-print-settings", "paper=A5,landscape,fit",
        "-silent", "{file}",
    ]


def agent_environment(sumatra_path, printer_name, server_url="", token="", poll_seconds=60):
    env = os.environ.copy()
    env["YECAI_PRINT_COMMAND_JSON"] = json.dumps(print_command(sumatra_path, printer_name), ensure_ascii=False)
    env["YECAI_SERVER_URL"] = server_url.strip()
    env["YECAI_PRINT_AGENT_TOKEN"] = token.strip()
    env["YECAI_PRINT_POLL_SECONDS"] = str(max(30, int(poll_seconds)))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class PlacementPrintDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("业财系统 · 仓库作业单自动打印")
        self.geometry("780x620")
        self.minsize(720, 570)
        try:
            saved = load_settings()
            load_warning = ""
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            saved = {}
            load_warning = f"本地配置读取失败，请重新填写并保存：{exc}"
        self.printer = tk.StringVar(value=saved.get("printer") or DEFAULT_PRINTER)
        self.printer_query = tk.StringVar()
        configured_sumatra = saved.get("sumatra") or find_sumatra()
        if configured_sumatra and not sumatra_is_printable(
            Path(configured_sumatra) if Path(configured_sumatra).is_absolute() else ROOT / configured_sumatra
        ):
            corrected = find_sumatra()
            if corrected:
                configured_sumatra = corrected
                load_warning = "原保存的 SumatraPDF 路径实际指向安装包，已改选安装目录中的打印程序；请重新保存配置。"
        self.sumatra = tk.StringVar(value=configured_sumatra)
        configured_server = saved.get("server") or os.environ.get("YECAI_SERVER_URL", DEFAULT_SERVER_URL)
        migrated_server = current_server_url(configured_server)
        if str(configured_server or "").strip().rstrip("/") in LEGACY_SERVER_URLS:
            load_warning = f"原服务器地址为不安全的 HTTP，已切换为 {DEFAULT_SERVER_URL}；请保存配置。"
        self.server = tk.StringVar(value=migrated_server)
        self.token = tk.StringVar(value=saved.get("token", ""))
        self.poll = tk.StringVar(value=str(saved.get("poll", 60)))
        self.autostart = tk.BooleanVar(value=is_autostart_enabled())
        self.events = queue.Queue()
        self.printer_names = []
        self.agent_process = None
        self.one_shot_running = False
        self.user_stopping = False
        self.busy = False
        self.auth_error_shown = False
        self._build()
        if load_warning:
            self._append(load_warning)
        elif saved:
            self._append("已读取上次保存的配置；认证码由当前 Windows 用户解密。")
        self._refresh_printers()
        self.after(200, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)
        if "--autostart" in sys.argv[1:]:
            self.after(350, self._auto_start_printing)

    def _build(self):
        form = ttk.Frame(self, padding=18)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        rows = [
            ("Windows 打印机", self.printer, False),
            ("搜索打印机", self.printer_query, False),
            ("SumatraPDF.exe", self.sumatra, False),
            ("服务器地址", self.server, False),
            ("设备认证码", self.token, True),
            ("轮询间隔（秒）", self.poll, False),
        ]
        for row, (label, variable, secret) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            if row == 0:
                self.printer_box = ttk.Combobox(form, textvariable=variable, state="readonly")
                self.printer_box.grid(row=row, column=1, sticky="ew", pady=6)
            else:
                ttk.Entry(form, textvariable=variable, show="*" if secret else "").grid(
                    row=row, column=1, sticky="ew", pady=6
                )
        self.printer_query.trace_add("write", lambda *_args: self._filter_printer_choices())
        ttk.Button(form, text="刷新全部", command=self._refresh_printers).grid(row=1, column=2, padx=(8, 0))
        program_buttons = ttk.Frame(form)
        program_buttons.grid(row=2, column=2, padx=(8, 0))
        ttk.Button(program_buttons, text="选择程序", command=self._choose_sumatra).pack(side="left")
        ttk.Button(program_buttons, text="官方下载", command=lambda: webbrowser.open(SUMATRA_URL)).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(form, text="打开设备管理", command=lambda: webbrowser.open(ADMIN_URL)).grid(
            row=4, column=2, padx=(8, 0)
        )
        ttk.Checkbutton(
            form, text="登录 Windows 后自动启动并开始打印（勾选后点“保存配置”生效）",
            variable=self.autostart,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(
            form,
            text="认证码在本机使用 Windows 当前用户密钥加密保存；远程服务器须使用 HTTPS。",
            foreground="#9a5a00",
            wraplength=680,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(
            form,
            text="先试打匿名示例单。确认 A5 横向出纸后，再连接服务器自动领取置货单和发货单。",
            wraplength=680,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(self, padding=(18, 0, 18, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存配置", command=self._save_settings).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="试打印示例单", command=self._test_print).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="单次领取并打印", command=lambda: self._start(once=True)).pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(buttons, text="开始自动打印", command=self._start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")

        log_frame = ttk.Frame(self, padding=(18, 0, 18, 18))
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="运行记录（不会显示认证码）").pack(anchor="w")
        self.log = tk.Text(log_frame, height=14, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        self._append("打印机名称是默认值或上次保存的选择；下拉框会列出 Windows 中实际安装的打印机。")
        if not self.sumatra.get():
            self._append("未找到 SumatraPDF.exe；请安装并使用“选择程序”定位它。")

    def _append(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _choose_sumatra(self):
        path = filedialog.askopenfilename(title="选择 SumatraPDF.exe", filetypes=[("程序", "*.exe")])
        if path:
            self.sumatra.set(path)

    def _filter_printer_choices(self):
        self.printer_box.configure(values=filter_printers(self.printer_names, self.printer_query.get()))

    def _refresh_printers(self):
        self.printer_query.set("")
        self._append("正在读取 Windows 已安装的打印机……")

        def work():
            try:
                self.events.put(("printers", list_installed_printers()))
            except OSError as exc:
                self.events.put(("printer_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _save_settings(self):
        try:
            print_command(self.sumatra.get(), self.printer.get())
            poll = max(30, int(self.poll.get()))
            self.poll.set(str(poll))
            if self.autostart.get():
                from scripts.placement_print_agent import server_url_is_safe

                if not server_url_is_safe(self.server.get().strip()):
                    raise ValueError("启用开机自动打印前，请先配置 HTTPS 服务器地址")
                if not self.token.get().strip():
                    raise ValueError("启用开机自动打印前，请先填写设备认证码")
            settings = {
                "printer": self.printer.get(), "sumatra": self.sumatra.get(),
                "server": self.server.get(), "token": self.token.get(), "poll": poll,
            }
            previous = is_autostart_enabled()
            desired = self.autostart.get()
            if desired != previous:
                set_autostart(desired, __file__)
            try:
                path = save_settings(settings)
            except Exception:
                if desired != previous:
                    set_autostart(previous, __file__)
                raise
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self._append(f"配置已保存到 {path}；开机启动{'已启用' if desired else '未启用'}。")
        if self.token.get().strip():
            fingerprint = hashlib.sha256(self.token.get().strip().encode("utf-8")).hexdigest()[:12]
            self._append(f"当前打印设备认证码指纹：{fingerprint}（可与系统设备管理页核对）。")
        messagebox.showinfo("保存成功", "下次打开会自动读取配置。正在运行的轮询如需应用新配置，请停止后重新开始。")

    def _environment(self, require_server):
        env = agent_environment(
            self.sumatra.get(), self.printer.get(),
            self.server.get() if require_server else "",
            self.token.get() if require_server else "",
            self.poll.get(),
        )
        if require_server:
            from scripts.placement_print_agent import server_url_is_safe

            if not server_url_is_safe(env["YECAI_SERVER_URL"]):
                raise ValueError("远程服务器必须使用 HTTPS；当前 HTTP 地址不能安全发送设备认证码")
            if not env["YECAI_PRINT_AGENT_TOKEN"]:
                raise ValueError("请输入管理员创建的打印设备认证码")
        return env

    def _test_print(self):
        if self.busy or self.agent_process is not None:
            return
        try:
            env = self._environment(require_server=False)
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        self.busy = True
        self._append("正在生成匿名置货单并提交试打印……")

        def work():
            try:
                from scripts.preview_order_printing import generate_sample_pdfs

                generate_sample_pdfs(SAMPLE_PDF.parent)
                log_path = _child_log_path() if is_frozen_app() else None
                if log_path:
                    env["YECAI_CHILD_LOG_PATH"] = str(log_path)
                result = subprocess.run(
                    agent_command("--test-print", str(SAMPLE_PDF)),
                    cwd=ROOT, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=240,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = _read_child_log(log_path) if log_path else (result.stdout + result.stderr).strip()
                if log_path:
                    log_path.unlink(missing_ok=True)
                self.events.put(("test_done", result.returncode, output))
            except (OSError, subprocess.SubprocessError) as exc:
                self.events.put(("test_done", 1, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _start(self, once=False):
        if self.busy or self.agent_process is not None:
            return False
        try:
            env = self._environment(require_server=True)
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc))
            return False
        command = agent_command()
        if once:
            command.append("--once")
        log_path = _child_log_path() if is_frozen_app() else None
        if log_path:
            env["YECAI_CHILD_LOG_PATH"] = str(log_path)
        try:
            process = subprocess.Popen(
                command, cwd=ROOT, env=env,
                stdout=subprocess.DEVNULL if log_path else subprocess.PIPE,
                stderr=subprocess.DEVNULL if log_path else subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            return False
        self.agent_process = process
        self.auth_error_shown = False
        self.one_shot_running = once
        self.user_stopping = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._append("正在领取一次打印任务……" if once else "自动打印已启动，等待新的置货单或发货单……")

        def monitor():
            if log_path:
                position = 0
                while process.poll() is None:
                    if log_path.is_file():
                        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                            handle.seek(position)
                            for line in handle:
                                self.events.put(("log", line.rstrip()))
                            position = handle.tell()
                    time.sleep(0.25)
                if log_path.is_file():
                    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(position)
                        for line in handle:
                            self.events.put(("log", line.rstrip()))
                    log_path.unlink(missing_ok=True)
            else:
                assert process.stdout is not None
                for line in process.stdout:
                    self.events.put(("log", line.rstrip()))
            self.events.put(("exit", process, process.wait()))

        threading.Thread(target=monitor, daemon=True).start()
        return True

    def _auto_start_printing(self):
        if self._start():
            self.iconify()

    def _stop(self):
        process = self.agent_process
        if process and process.poll() is None:
            self.user_stopping = True
            process.terminate()
            self._append("正在停止自动打印……")

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    message = event[1]
                    is_auth_error = "[AUTH_ERROR]" in message
                    self._append(message.replace("[AUTH_ERROR]", "").strip())
                    if is_auth_error and not self.auth_error_shown:
                        self.auth_error_shown = True
                        self.deiconify()
                        messagebox.showerror(
                            "打印设备认证失败",
                            "服务器拒绝了当前打印认证码。\n\n"
                            "请到【系统管理 → 打印设备与记录】的“打印设备认证码”区域生成新码，"
                            "不要使用下方的“仓库扫码设备”认证码。\n\n"
                            "填入新码并保存后，点击“停止”，再重新开始自动打印。",
                        )
                elif event[0] == "printers":
                    self.printer_names = event[1]
                    self._filter_printer_choices()
                    if self.printer_names and self.printer.get() not in self.printer_names:
                        self.printer.set(self.printer_names[0])
                    self._append(f"发现 {len(self.printer_names)} 台已安装打印机，均已加入下拉列表；点击右侧箭头可查看。")
                elif event[0] == "printer_error":
                    self._append(f"打印机列表读取失败：{event[1]}。仍可手动输入打印机名称。")
                elif event[0] == "test_done":
                    self.busy = False
                    self._append(event[2] or f"试打印程序退出码：{event[1]}")
                    if event[1] == 0:
                        messagebox.showinfo("试打印命令已完成", "请检查 Windows 打印队列、实际出纸和 A5 横向版式。")
                    else:
                        messagebox.showerror("试打印失败", "请查看窗口下方的运行记录。")
                elif event[0] == "exit" and self.agent_process is event[1]:
                    self.agent_process = None
                    self.one_shot_running = False
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._append(f"自动打印进程已退出（代码 {event[2]}）。")
                    if event[2] != 0 and not self.user_stopping and self.state() == "iconic":
                        self.deiconify()
                        messagebox.showerror("自动打印已停止", "打印进程意外退出，请查看运行记录。")
        except queue.Empty:
            pass
        self.after(200, self._drain_events)

    def _close(self):
        if self.busy or self.one_shot_running:
            messagebox.showwarning("请稍候", "当前任务仍在处理，请完成后再关闭窗口。")
            return
        choice = messagebox.askyesnocancel(
            "关闭仓库作业单打印窗口",
            "是否继续在后台自动打印？\n\n是：最小化到任务栏，打印继续运行（点击任务栏图标可恢复）。\n否：停止打印并退出程序。\n取消：返回窗口。",
        )
        if choice is None:
            return
        if choice:
            if self.agent_process is None and not self._start():
                return
            self.iconify()
        else:
            self._stop()
            self.destroy()


def main():
    if "--agent" in sys.argv[1:]:
        log_path = os.environ.get("YECAI_CHILD_LOG_PATH")
        stream = None
        if log_path:
            stream = open(log_path, "a", encoding="utf-8", buffering=1)
            sys.stdout = stream
            sys.stderr = stream
        try:
            from scripts.placement_print_agent import main as agent_main

            marker = sys.argv.index("--agent")
            agent_main(sys.argv[marker + 1:])
        finally:
            if stream:
                stream.close()
        return
    PlacementPrintDesktop().mainloop()


if __name__ == "__main__":
    main()
