"""Encrypted per-user settings for the Windows warehouse scan workstation."""

import base64
import binascii
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import winreg
from ctypes import wintypes
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "YecaiWarehouseScanner"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise OSError("仓库扫码认证码只能在 Windows 上加密保存")
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob_in, blob_out = DataBlob(len(data), source), DataBlob()
    api = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    if protect:
        function = api.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
    else:
        function = api.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
    function.restype = wintypes.BOOL
    if protect:
        result = function(ctypes.byref(blob_in), "业财仓库扫码设备认证码", None, None, None, 0x1, ctypes.byref(blob_out))
    else:
        result = function(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.data, blob_out.size)
    finally:
        local_free = ctypes.WinDLL("kernel32.dll").LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(blob_out.data)


def _encrypt(token: str) -> str:
    return base64.b64encode(_crypt(token.encode("utf-8"), True)).decode("ascii") if token else ""


def _decrypt(encoded: str) -> str:
    return _crypt(base64.b64decode(encoded, validate=True), False).decode("utf-8") if encoded else ""


def config_path() -> Path:
    app_data = os.environ.get("LOCALAPPDATA")
    if not app_data:
        raise OSError("无法找到当前 Windows 用户的 LOCALAPPDATA 目录")
    return Path(app_data) / "YecaiWarehouseScanner" / "settings.json"


def load_settings() -> dict:
    target = config_path()
    if not target.is_file():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("本地扫码配置版本不受支持")
    try:
        token = _decrypt(data.get("token_dpapi", ""))
    except (binascii.Error, OSError, UnicodeError, ValueError) as exc:
        raise ValueError("认证码无法由当前 Windows 用户解密，请重新输入") from exc
    return {
        "server": data.get("server", ""),
        "token": token,
        "photo_dir": data.get("photo_dir", ""),
        "max_photo_age_minutes": int(data.get("max_photo_age_minutes", 10)),
    }


def save_settings(settings: dict) -> Path:
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "server": str(settings["server"]).strip().rstrip("/"),
        "token_dpapi": _encrypt(str(settings["token"]).strip()),
        "photo_dir": str(settings["photo_dir"]).strip(),
        "max_photo_age_minutes": max(1, min(60, int(settings["max_photo_age_minutes"]))),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix="settings-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return target


def startup_command(script_path: Path) -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([str(Path(sys.executable).resolve()), "--autostart"])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.is_file() else Path(sys.executable)
    return subprocess.list2cmdline([str(executable), str(script_path.resolve()), "--autostart"])


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool, script_path: Path) -> None:
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command(script_path))
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass
