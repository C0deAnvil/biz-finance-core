"""Per-user settings for the Windows placement printer desktop app.

The print-agent token is encrypted with Windows DPAPI for the current user.
Autostart uses only our own HKCU Run value and is changed only on Save.
"""

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
RUN_VALUE = "YecaiPlacementPrinter"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt_blob(data, protect):
    if sys.platform != "win32":
        raise OSError("设备认证码只能在 Windows 上加密保存")
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob_in = DataBlob(len(data), source)
    blob_out = DataBlob()
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
        result = function(ctypes.byref(blob_in), "业财置货打印设备认证码", None, None, None, 0x1, ctypes.byref(blob_out))
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


def encrypt_token(token):
    return base64.b64encode(_crypt_blob(token.encode("utf-8"), True)).decode("ascii") if token else ""


def decrypt_token(encoded):
    return _crypt_blob(base64.b64decode(encoded, validate=True), False).decode("utf-8") if encoded else ""


def config_path():
    app_data = os.environ.get("LOCALAPPDATA")
    if not app_data:
        raise OSError("无法找到当前 Windows 用户的 LOCALAPPDATA 目录")
    return Path(app_data) / "YecaiPlacementPrinter" / "settings.json"


def load_settings(path=None):
    target = Path(path) if path is not None else config_path()
    if not target.is_file():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("本地打印配置版本不受支持")
    try:
        token = decrypt_token(data.get("token_dpapi", ""))
    except (binascii.Error, OSError, UnicodeError, ValueError) as exc:
        raise ValueError("认证码无法由当前 Windows 用户解密，请重新输入并保存") from exc
    return {
        "printer": data.get("printer", ""),
        "sumatra": data.get("sumatra", ""),
        "server": data.get("server", ""),
        "token": token,
        "poll": data.get("poll", 60),
    }


def save_settings(settings, path=None):
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "printer": str(settings["printer"]).strip(),
        "sumatra": str(settings["sumatra"]).strip(),
        "server": str(settings["server"]).strip(),
        "token_dpapi": encrypt_token(str(settings["token"]).strip()),
        "poll": max(30, int(settings["poll"])),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix="settings-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def startup_command(script_path):
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([str(Path(sys.executable).resolve()), "--autostart"])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.is_file() else Path(sys.executable)
    return subprocess.list2cmdline([str(executable), str(Path(script_path).resolve()), "--autostart"])


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return False


def set_autostart(enabled, script_path):
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command(script_path))
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass
