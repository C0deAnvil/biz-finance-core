"""Windows warehouse workstation for HID barcode guns and shipment photos.

The OY10 Plus should use USB-HID keyboard mode and append Enter.  The window
keeps a large scan box focused, so no vendor SDK is required.
"""

from __future__ import annotations

import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
import uuid

from curl_cffi import CurlMime, requests


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.warehouse_scan_config import (  # noqa: E402
    is_autostart_enabled, load_settings, save_settings, set_autostart,
)


DEFAULT_SERVER = "https://www.cphwchem.xyz:8001"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
BARCODE_RE = re.compile(r"^(?:YCP01|YCS01)[A-F0-9]{24}$")
MAX_PROOF_BYTES = 10 * 1024 * 1024


def api_error_message(body: dict, http_status: int) -> str:
    """Display both business errors and FastAPI validation/HTTP errors."""
    detail = body.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, list):
        issues = []
        for item in detail:
            if isinstance(item, dict):
                field = " / ".join(str(part) for part in item.get("loc", []) if part != "body")
                issues.append(f"{field}：{item.get('msg', '输入无效')}" if field else str(item.get("msg", "输入无效")))
        if issues:
            return "；".join(issues)
    message = body.get("msg") or body.get("message")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    conditions = data.get("blocked_conditions")
    if conditions:
        return "；".join(str(condition) for condition in conditions)
    shortages = data.get("shortages")
    if shortages:
        details = []
        for row in shortages:
            if isinstance(row, dict):
                name = row.get("product_name") or "商品"
                item_type = row.get("packaging_name") or ("包装" if row.get("shortage_type") == "packaging" else "成品")
                details.append(f"{name}的{item_type}缺 {row.get('shortage_quantity', 0)}")
        if details:
            return f"{message or '库存不足'}：{'；'.join(details)}"
    return str(message or f"请求失败（HTTP {http_status}）")


def activate_english_keyboard(window_id: int | None = None) -> None:
    """Keep HID scanner keystrokes out of the Chinese IME composition path."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
        user32.LoadKeyboardLayoutW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
        layout = user32.LoadKeyboardLayoutW("00000409", 0x00000001)  # English (United States)
        if not layout:
            return
        user32.ActivateKeyboardLayout(ctypes.c_void_p(layout), 0)
        if window_id:
            # WM_INPUTLANGCHANGEREQUEST applies the layout to the Tk window.
            user32.PostMessageW(window_id, 0x0050, 0, ctypes.c_void_p(layout))
    except (AttributeError, OSError, TypeError, ValueError):
        # Failure to switch layouts must not prevent manual operation.
        return


def safe_server(url: str) -> bool:
    parsed = urlsplit(url.strip())
    return (parsed.scheme == "https" and bool(parsed.hostname)) or (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    )


class WarehouseScanDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("业财系统 · 仓库扫码作业")
        self.geometry("900x650")
        self.minsize(780, 580)
        try:
            saved = load_settings()
            warning = ""
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            saved, warning = {}, f"读取配置失败：{exc}"
        self.server = tk.StringVar(value=saved.get("server") or DEFAULT_SERVER)
        self.token = tk.StringVar(value=saved.get("token") or "")
        self.photo_dir = tk.StringVar(value=saved.get("photo_dir") or str(ROOT / "outbound_img"))
        self.max_photo_age = tk.StringVar(value=str(saved.get("max_photo_age_minutes", 10)))
        self.autostart = tk.BooleanVar(value=is_autostart_enabled())
        self.scan_value = tk.StringVar()
        self.events = queue.Queue()
        self.busy = False
        self.scan_submit_after = None
        self.used_photo_hashes: set[str] = set()
        self._build()
        self.after(120, self._drain)
        self.after(200, self._focus_scan)
        if warning:
            self._status(False, warning)
        elif "--autostart" in sys.argv[1:]:
            self._status(True, "扫码程序已随 Windows 启动，等待扫描作业单")

    def _build(self):
        settings_frame = ttk.LabelFrame(self, text="本机配置", padding=14)
        settings_frame.pack(fill="x", padx=16, pady=(16, 8))
        settings_frame.columnconfigure(1, weight=1)
        rows = [("服务器", self.server, False), ("扫码设备认证码", self.token, True), ("高拍仪照片目录", self.photo_dir, False), ("照片最大有效分钟", self.max_photo_age, False)]
        for index, (label, variable, secret) in enumerate(rows):
            ttk.Label(settings_frame, text=label).grid(row=index, column=0, sticky="w", padx=(0, 10), pady=4)
            ttk.Entry(settings_frame, textvariable=variable, show="*" if secret else "").grid(row=index, column=1, sticky="ew", pady=4)
        ttk.Button(settings_frame, text="选择目录", command=self._choose_photo_dir).grid(row=2, column=2, padx=(8, 0))
        ttk.Checkbutton(settings_frame, text="登录 Windows 后自动启动", variable=self.autostart).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(settings_frame, text="保存配置", command=self._save).grid(row=4, column=2, padx=(8, 0), pady=(6, 0))

        scan_frame = ttk.LabelFrame(self, text="扫描区（扫描枪设置为回车结尾）", padding=16)
        scan_frame.pack(fill="x", padx=16, pady=8)
        self.scan_entry = ttk.Entry(scan_frame, textvariable=self.scan_value, font=("Microsoft YaHei UI", 18))
        self.scan_entry.pack(fill="x")
        self.scan_entry.bind("<Return>", self._scan)
        self.scan_value.trace_add("write", self._scan_value_changed)
        ttk.Label(scan_frame, text="扫描框会自动切换到英文输入；有无回车均可。置货码直接整单置货，发货码上传最新照片后整单发货。", foreground="#555").pack(anchor="w", pady=(8, 0))

        self.result = tk.Label(self, text="等待扫描", font=("Microsoft YaHei UI", 25, "bold"), bg="#E8F4FF", fg="#1D5F91", pady=24, wraplength=820)
        self.result.pack(fill="x", padx=16, pady=8)
        log_frame = ttk.LabelFrame(self, text="本机作业记录", padding=10)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self.log = tk.Text(log_frame, state="disabled", height=10, wrap="word")
        self.log.pack(fill="both", expand=True)
        self._append("认证码只保存在本机并由 Windows 当前用户密钥加密；条码中不包含认证码。")

    def _choose_photo_dir(self):
        folder = filedialog.askdirectory(title="选择高拍仪照片保存目录", initialdir=self.photo_dir.get())
        if folder:
            self.photo_dir.set(folder)

    def _save(self):
        try:
            if not safe_server(self.server.get()):
                raise ValueError("远程服务器必须使用 HTTPS")
            if not self.token.get().strip():
                raise ValueError("请填写扫码设备认证码")
            photo_dir = Path(self.photo_dir.get().strip())
            photo_dir.mkdir(parents=True, exist_ok=True)
            age = max(1, min(60, int(self.max_photo_age.get())))
            save_settings({"server": self.server.get(), "token": self.token.get(), "photo_dir": str(photo_dir), "max_photo_age_minutes": age})
            set_autostart(self.autostart.get(), Path(__file__))
            self._status(True, "配置已保存")
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc))
        finally:
            self._focus_scan()

    def _focus_scan(self):
        self.scan_entry.focus_force()
        activate_english_keyboard(self.winfo_id())
        self.scan_entry.selection_range(0, "end")

    def _scan_value_changed(self, *_args):
        if self.scan_submit_after is not None:
            self.after_cancel(self.scan_submit_after)
            self.scan_submit_after = None
        barcode = "".join(self.scan_value.get().strip().upper().split())
        if BARCODE_RE.fullmatch(barcode):
            # HID scanners may omit the Enter suffix.  A short debounce waits
            # for the complete 29-character value before submitting once.
            self.scan_submit_after = self.after(120, self._scan)

    def _scan(self, _event=None):
        if self.scan_submit_after is not None:
            self.after_cancel(self.scan_submit_after)
            self.scan_submit_after = None
        barcode = "".join(self.scan_value.get().strip().upper().split())
        self.scan_value.set("")
        if self.busy:
            self._status(False, "上一笔仍在处理中，请稍候")
            return
        if not BARCODE_RE.fullmatch(barcode):
            self._status(False, "条码应为 YCP01/YCS01 加24位字符；请切换英文输入法后只扫描一个完整条码")
            return
        config = {
            "server": self.server.get().strip().rstrip("/"),
            "token": self.token.get().strip(),
            "photo_dir": self.photo_dir.get().strip(),
            "max_photo_age_minutes": self.max_photo_age.get().strip(),
        }
        self.busy = True
        self._status(None, "正在校验并处理，请勿重复扫描……")
        threading.Thread(target=self._process, args=(barcode, config), daemon=True).start()

    def _api(self, path: str, config: dict, *, payload=None, files=None, data=None):
        server = config["server"]
        token = config["token"]
        if not safe_server(server) or not token:
            raise RuntimeError("服务器或认证码尚未正确配置")
        headers = {"X-Warehouse-Device-Token": token, "User-Agent": "YecaiWarehouseScanner/1.0"}
        multipart = None
        try:
            request_kwargs = {
                "headers": headers,
                "timeout": 30,
                "impersonate": "chrome",
            }
            if files:
                multipart = CurlMime()
                for name, value in (data or {}).items():
                    multipart.addpart(name=name, data=str(value).encode("utf-8"))
                for name, file_info in files.items():
                    filename, handle, content_type = file_info
                    multipart.addpart(
                        name=name,
                        filename=filename,
                        content_type=content_type,
                        data=handle.read(),
                    )
                request_kwargs["multipart"] = multipart
            else:
                request_kwargs["json"] = payload
            # Keep the scanner consistent with the working print agent.  The
            # deployed HTTPS endpoint may close the generic libcurl/BoringSSL
            # handshake, while a browser-compatible TLS fingerprint succeeds.
            # Certificate verification remains enabled.
            response = requests.post(
                f"{server}/api/warehouse-scanning{path}",
                **request_kwargs,
            )
        except requests.RequestsError as exc:
            message = str(exc)
            if "SSL" in message or "BoringSSL" in message or "curl: (35)" in message:
                raise RuntimeError(
                    "HTTPS 安全连接失败。请确认服务器地址是 "
                    "https://www.cphwchem.xyz:8001；如果浏览器可以访问，"
                    "请更新并重新启动仓库扫码客户端。"
                ) from exc
            raise RuntimeError(f"连接服务器失败：{message}") from exc
        finally:
            if multipart is not None:
                multipart.close()
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError(f"服务器返回了无法识别的内容（HTTP {response.status_code}）") from exc
        if response.status_code >= 400 or body.get("code") != 200:
            raise RuntimeError(api_error_message(body, response.status_code))
        return body.get("data") or {}

    def _latest_photo(self, config: dict) -> tuple[Path, str]:
        folder = Path(config["photo_dir"])
        if not folder.is_dir():
            raise RuntimeError("高拍仪照片目录不存在")
        candidates = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("照片目录中没有 JPG/PNG 发货凭证")
        max_age = max(1, min(60, int(config["max_photo_age_minutes"]))) * 60
        for path in candidates:
            before = path.stat()
            age = time.time() - before.st_mtime
            time.sleep(0.4)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("最新照片仍在写入，请稍等一秒后重新扫描")
            if age <= max_age and after.st_size == 0:
                raise RuntimeError(f"最新照片 {path.name} 是空文件，请重新拍摄")
            if age <= max_age and after.st_size > MAX_PROOF_BYTES:
                raise RuntimeError(f"最新照片 {path.name} 大小为 {after.st_size / 1024 / 1024:.1f}MB，超过 10MB 上限；请压缩或重拍")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if age <= max_age and digest not in self.used_photo_hashes:
                return path, digest
        raise RuntimeError(f"没有找到 {int(max_age / 60)} 分钟内拍摄且尚未使用的照片，请先拍照")

    def _process(self, barcode: str, config: dict):
        try:
            resolved = self._api("/device/resolve", config, payload={"barcode": barcode})
            if resolved.get("completed"):
                self.events.put((True, resolved.get("message") or "该作业已经完成"))
                return
            if not resolved.get("allowed"):
                raise RuntimeError(resolved.get("message") or "当前条件不允许处理")
            key = uuid.uuid4().hex
            if resolved.get("action") == "placement":
                result = self._api("/device/placement/confirm", config, payload={"barcode": barcode, "idempotency_key": key})
            else:
                photo, digest = self._latest_photo(config)
                captured_at = datetime.fromtimestamp(photo.stat().st_mtime).astimezone().isoformat()
                with photo.open("rb") as handle:
                    proof = self._api(
                        "/device/shipment-proofs",
                        config,
                        files={"file": (photo.name, handle, "image/png" if photo.suffix.lower() == ".png" else "image/jpeg")},
                        data={"barcode": barcode, "captured_at": captured_at},
                    )
                result = self._api("/device/shipment/confirm", config, payload={"barcode": barcode, "proof_id": proof["proof_id"], "idempotency_key": key})
                self.used_photo_hashes.add(digest)
            self.events.put((True, result.get("message") or resolved.get("message") or "处理成功"))
        except Exception as exc:
            self.events.put((False, str(exc)))
        finally:
            self.events.put(("done", ""))

    def _drain(self):
        try:
            while True:
                success, message = self.events.get_nowait()
                if success == "done":
                    self.busy = False
                    self._focus_scan()
                else:
                    self._status(success, message)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _status(self, success, message: str):
        if success is True:
            self.result.configure(text=f"✓ {message}", bg="#DFF3E4", fg="#18733A")
        elif success is False:
            self.result.configure(text=f"✕ {message}", bg="#FDE2E2", fg="#B42318")
        else:
            self.result.configure(text=message, bg="#FFF4D6", fg="#8A5500")
        self._append(message)

    def _append(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    WarehouseScanDesktop().mainloop()
