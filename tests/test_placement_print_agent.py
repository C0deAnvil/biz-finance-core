"""Local print-agent checks do not submit anything to a real printer."""

import json
import subprocess
from types import SimpleNamespace
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


def test_agent_request_uses_browser_compatible_tls(monkeypatch):
    monkeypatch.setattr(agent, "BASE_URL", "https://print.example")
    monkeypatch.setattr(agent, "TOKEN", "secret-token")
    response = SimpleNamespace(
        status_code=200,
        reason="OK",
        headers={},
        content=b'{"data":{"id":7}}',
        json=lambda: {"data": {"id": 7}},
    )
    with patch.object(agent.curl_requests, "request", return_value=response) as request:
        assert agent.request("/claim", method="POST", data={"recover_failed": True}) == {"id": 7}
    request.assert_called_once_with(
        "POST",
        "https://print.example/claim",
        headers={"X-Print-Agent-Token": "secret-token", "User-Agent": agent.USER_AGENT},
        json={"recover_failed": True},
        timeout=30,
        impersonate="chrome",
    )


def test_agent_request_preserves_server_authentication_message(monkeypatch):
    monkeypatch.setattr(agent, "BASE_URL", "https://print.example")
    monkeypatch.setattr(agent, "TOKEN", "invalid-token")
    response = SimpleNamespace(
        status_code=401,
        reason="Unauthorized",
        headers={},
        content='{"code":401,"msg":"打印设备密钥无效、缺失或已停用"}'.encode("utf-8"),
        json=lambda: {"code": 401, "msg": "打印设备密钥无效、缺失或已停用"},
    )
    with patch.object(agent.curl_requests, "request", return_value=response), pytest.raises(
        agent.PrintAgentHTTPError, match="打印设备密钥无效"
    ) as exc_info:
        agent.request("/api/printing/agent/jobs/claim", method="POST", data={})

    assert exc_info.value.status_code == 401


def test_local_pdf_submission_uses_configured_printer_without_a_shell(tmp_path, monkeypatch):
    pdf = tmp_path / "试打印.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("YECAI_PRINT_COMMAND_JSON", json.dumps(["printer.exe", "-silent", "{file}"]))
    with patch.object(agent.subprocess, "run") as run:
        result = agent.submit_pdf_to_printer(pdf)
    assert result == "submitted_only"
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


def test_startup_claim_requests_failed_job_recovery():
    with patch.object(agent, "request", return_value=None) as request:
        assert agent.run_once(recover_failed=True) is None
    request.assert_called_once_with(
        "/api/printing/agent/jobs/claim",
        method="POST",
        data={"recover_failed": True},
    )


@pytest.mark.parametrize("status,message", [
    ("PaperOut", "缺纸"),
    ("Offline", "离线"),
    ("Error, UserIntervention", "错误"),
    ("Jammed", "卡纸"),
])
def test_printer_problem_explains_windows_spooler_failure(status, message):
    assert message in agent._printer_problem(status)


def test_run_once_stops_backlog_when_driver_cannot_verify_output():
    job = {"id": 7, "order_id": 8, "lease_token": "lease", "pdf_path": "/pdf"}
    with patch.object(agent, "request", side_effect=[job, {"status": "submitted"}]) as request, \
            patch.object(agent, "print_job", return_value="submitted_only"):
        assert agent.run_once() is False
    assert request.call_args_list[-1].kwargs["data"]["verification"] == "submitted_only"


def test_spooler_verification_waits_until_observed_job_leaves_queue():
    snapshots = [
        {"printer_status": "", "jobs": {91: {"document": "placement.pdf", "status": "Printing"}}},
        {"printer_status": "", "jobs": {}},
    ]
    with patch.object(agent, "_windows_spooler_snapshot", side_effect=snapshots), \
            patch.object(agent.time, "sleep"):
        assert agent._wait_for_windows_spooler("Test Printer", set()) == "spooler_completed"


def test_spooler_verification_rejects_paper_out():
    snapshot = {
        "printer_status": "PaperOut",
        "jobs": {91: {"document": "placement.pdf", "status": "PaperOut,UserIntervention"}},
    }
    with patch.object(agent, "_windows_spooler_snapshot", return_value=snapshot), \
            pytest.raises(RuntimeError, match="缺纸"):
        agent._wait_for_windows_spooler("Test Printer", set())
