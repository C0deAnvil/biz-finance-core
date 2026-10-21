"""Local print-agent checks do not submit anything to a real printer."""

import json
import subprocess
from unittest.mock import patch

import pytest

from scripts import placement_print_agent as agent


@pytest.mark.parametrize("url,expected", [
    ("https://example.com", True),
    ("http://127.0.0.1:8000", True),
    ("http://[::1]:8000", True),
    ("http://8.140.196.85", False),
    ("http://127.0.0.1.evil.example", False),
    ("", False),
])
def test_server_url_does_not_expose_token_over_remote_http(url, expected):
    assert agent.server_url_is_safe(url) is expected


def test_local_pdf_submission_uses_configured_printer_without_a_shell(tmp_path, monkeypatch):
    pdf = tmp_path / "试打印.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", json.dumps(["printer.exe", "-silent", "{file}"]))
    with patch.object(agent.subprocess, "run") as run:
        agent.submit_pdf_to_printer(pdf)
    run.assert_called_once_with(["printer.exe", "-silent", str(pdf)], check=True, timeout=180)


def test_local_pdf_submission_rejects_non_pdf(tmp_path, monkeypatch):
    file = tmp_path / "invalid.pdf"
    file.write_text("not a PDF", encoding="utf-8")
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", '["printer.exe","{file}"]')
    with patch.object(agent.subprocess, "run") as run, pytest.raises(RuntimeError, match="不是 PDF"):
        agent.submit_pdf_to_printer(file)
    run.assert_not_called()


def test_print_command_requires_file_placeholder(monkeypatch):
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", '["printer.exe"]')
    with pytest.raises(RuntimeError, match="占位符"):
        agent.print_command()


def test_local_pdf_submission_explains_missing_printer(tmp_path, monkeypatch):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", '["printer.exe","{file}"]')
    with patch.object(agent.subprocess, "run", side_effect=subprocess.CalledProcessError(4, ["printer.exe"])):
        with pytest.raises(RuntimeError, match="找不到指定打印机"):
            agent.submit_pdf_to_printer(pdf)


def test_agent_rejects_renamed_sumatra_installer(tmp_path, monkeypatch):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    executable = tmp_path / "SumatraPDF.exe"
    executable.write_bytes(b"installer")
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", json.dumps([str(executable), "-print-to", "Test Printer", "{file}"]))
    with patch.object(agent.subprocess, "run") as run, pytest.raises(RuntimeError, match="改名后的安装包"):
        agent.submit_pdf_to_printer(pdf)
    run.assert_not_called()
