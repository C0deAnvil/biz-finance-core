"""Settings persistence and startup command tests; never alter real startup keys."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from scripts import placement_print_config as config
from scripts.placement_print_config import load_settings, save_settings, startup_command


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI requires Windows")
def test_settings_roundtrip_encrypts_token(tmp_path):
    path = tmp_path / "settings.json"
    original = {
        "printer": "EPSONB8696A (L3250 Series)",
        "sumatra": r"C:\Users\demo\AppData\Local\SumatraPDF\SumatraPDF.exe",
        "server": "https://example.com",
        "token": "test-only-secret-not-for-production",
        "poll": 45,
    }
    save_settings(original, path)
    raw = path.read_text(encoding="utf-8")
    assert original["token"] not in raw
    assert json.loads(raw)["token_dpapi"]
    assert load_settings(path) == original


def test_missing_settings_return_empty(tmp_path):
    assert load_settings(tmp_path / "missing.json") == {}


def test_startup_command_contains_no_token(tmp_path):
    command = startup_command(tmp_path / "placement_print_desktop.py")
    assert "--autostart" in command
    assert "placement_print_desktop.py" in command
    assert "YECAI_PRINT_AGENT_TOKEN" not in command


def test_enable_autostart_only_writes_own_current_user_value(tmp_path):
    key = MagicMock()
    with patch.object(config.winreg, "CreateKeyEx") as create, patch.object(config.winreg, "SetValueEx") as set_value:
        create.return_value.__enter__.return_value = key
        config.set_autostart(True, tmp_path / "placement_print_desktop.py")
    create.assert_called_once_with(config.winreg.HKEY_CURRENT_USER, config.RUN_KEY, 0, config.winreg.KEY_SET_VALUE)
    assert set_value.call_args.args[:4] == (key, config.RUN_VALUE, 0, config.winreg.REG_SZ)
    assert "--autostart" in set_value.call_args.args[4]


def test_invalid_encrypted_token_is_not_treated_as_plaintext(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 1, "token_dpapi": "not-valid-base64!"}), encoding="utf-8")
    with pytest.raises(ValueError, match="无法由当前 Windows 用户解密"):
        load_settings(path)
