"""Desktop launcher configuration checks; no GUI or printer is started."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from scripts.placement_print_desktop import (
    PlacementPrintDesktop, agent_environment, filter_printers, print_command, sumatra_is_printable,
)


def test_printer_search_is_case_insensitive_and_preserves_names():
    names = ["EPSONB8696A (L3250 Series)", "OneNote (Desktop)"]
    assert filter_printers(names, "l3250") == ["EPSONB8696A (L3250 Series)"]
    assert filter_printers(names, "  ") == names


def test_dropdown_filters_by_search_field_not_selected_printer():
    box = Mock()
    window = SimpleNamespace(
        printer_names=["EPSONB8696A (L3250 Series)", "OneNote (Desktop)"],
        printer=Mock(get=Mock(return_value="EPSONB8696A (L3250 Series)")),
        printer_query=Mock(get=Mock(return_value="")),
        printer_box=box,
    )
    PlacementPrintDesktop._filter_printer_choices(window)
    box.configure.assert_called_once_with(values=window.printer_names)


def test_epson_a5_print_command(tmp_path):
    exe = tmp_path / "SumatraPDF.exe"
    exe.write_bytes(b"test")
    (tmp_path / "libmupdf.dll").write_bytes(b"test")
    command = print_command(str(exe), "EPSONB8696A (L3250 Series)")
    assert command[1:] == [
        "-print-to", "EPSONB8696A (L3250 Series)",
        "-print-settings", "paper=A5,landscape,fit", "-silent", "{file}",
    ]


def test_environment_does_not_put_token_into_print_command(tmp_path):
    exe = tmp_path / "SumatraPDF.exe"
    exe.write_bytes(b"test")
    (tmp_path / "libmupdf.dll").write_bytes(b"test")
    env = agent_environment(str(exe), "EPSONB8696A (L3250 Series)", "https://example.com", "secret-token")
    assert env["YECAI_PRINT_AGENT_TOKEN"] == "secret-token"
    assert "secret-token" not in env["YECAI_PRINT_COMMAND_JSON"]
    assert json.loads(env["YECAI_PRINT_COMMAND_JSON"])[2] == "EPSONB8696A (L3250 Series)"


def test_print_command_rejects_missing_program(tmp_path):
    with pytest.raises(ValueError, match="SumatraPDF"):
        print_command(str(tmp_path / "missing.exe"), "EPSONB8696A (L3250 Series)")


def test_print_command_rejects_installer(tmp_path):
    installer = tmp_path / "SumatraPDF-3.6.1-64-install.exe"
    installer.write_bytes(b"test")
    with pytest.raises(ValueError, match="安装包"):
        print_command(str(installer), "EPSONB8696A (L3250 Series)")


def test_renamed_installer_without_engine_library_is_rejected(tmp_path):
    exe = tmp_path / "SumatraPDF.exe"
    exe.write_bytes(b"copied installer")
    assert not sumatra_is_printable(exe)
    with pytest.raises(ValueError, match="缺少配套 DLL"):
        print_command(str(exe), "EPSONB8696A (L3250 Series)")


@pytest.mark.parametrize("choice,should_minimize,should_exit", [
    (True, True, False), (False, False, True), (None, False, False),
])
def test_close_prompt_controls_background_or_exit(choice, should_minimize, should_exit):
    window = SimpleNamespace(
        busy=False, one_shot_running=False, agent_process=None,
        _start=Mock(return_value=True), iconify=Mock(), _stop=Mock(), destroy=Mock(),
    )
    with patch("scripts.placement_print_desktop.messagebox.askyesnocancel", return_value=choice):
        PlacementPrintDesktop._close(window)
    assert window.iconify.called is should_minimize
    assert window.destroy.called is should_exit
    assert window._stop.called is should_exit
