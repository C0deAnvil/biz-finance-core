"""Poll and silently print placement PDFs on a trusted printer computer.

Configuration is supplied through environment variables; the device token is
never written to a file or command-line argument by this program.
"""

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from ctypes import wintypes
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from curl_cffi import requests as curl_requests


BASE_URL = os.environ.get("YECAI_SERVER_URL", "").rstrip("/")
TOKEN = os.environ.get("YECAI_PRINT_AGENT_TOKEN", "")
POLL_SECONDS = max(30, int(os.environ.get("YECAI_PRINT_POLL_SECONDS", "60")))
VERIFY_SECONDS = max(30, min(240, int(os.environ.get("YECAI_PRINT_VERIFY_SECONDS", "180"))))
USER_AGENT = f"YecaiPlacementPrintAgent/1.0 ({platform.node()[:60]})"

PRINTER_ERROR_TOKENS = {
    "error": "打印机报告错误",
    "offline": "打印机离线",
    "paperout": "打印机缺纸",
    "outofpaper": "打印机缺纸",
    "paperproblem": "打印机纸张异常",
    "jammed": "打印机卡纸",
    "blocked": "打印任务被阻塞",
    "userintervention": "打印机需要人工处理",
    "paused": "打印机或打印任务已暂停",
}

PRINTER_STATUS_NAMES = {
    0x00000001: "Paused",
    0x00000002: "Error",
    0x00000008: "Jammed",
    0x00000010: "PaperOut",
    0x00000040: "PaperProblem",
    0x00000080: "Offline",
    0x00000800: "OutputBinFull",
    0x00001000: "NotAvailable",
    0x00040000: "NoToner",
    0x00100000: "UserIntervention",
    0x00200000: "OutOfMemory",
    0x00400000: "DoorOpen",
    0x02000000: "ServerOffline",
}

JOB_STATUS_NAMES = {
    0x0001: "Paused",
    0x0002: "Error",
    0x0004: "Deleting",
    0x0008: "Spooling",
    0x0010: "Printing",
    0x0020: "Offline",
    0x0040: "PaperOut",
    0x0080: "Printed",
    0x0100: "Deleted",
    0x0200: "Blocked",
    0x0400: "UserIntervention",
    0x0800: "Restart",
    0x1000: "Complete",
    0x2000: "Retained",
    0x4000: "Rendering",
}


def server_url_is_safe(url):
    """Never send the device token over remote plain HTTP."""
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and bool(parsed.hostname)) or (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
    )


def print_command():
    try:
        command = json.loads(os.environ.get("YECAI_PRINT_COMMAND_JSON", "[]"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("YECAI_PRINT_COMMAND_JSON 不是有效的 JSON 数组") from exc
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise RuntimeError("YECAI_PRINT_COMMAND_JSON 必须是非空的字符串数组")
    if not any("{file}" in part for part in command):
        raise RuntimeError("打印命令必须包含 {file} 占位符")
    return command


def _configured_printer_name(command):
    """Return the Windows printer named by Sumatra's ``-print-to`` option."""
    lowered = [part.casefold() for part in command]
    if "-print-to" not in lowered:
        return None
    index = lowered.index("-print-to") + 1
    return command[index] if index < len(command) else None


def _printer_problem(*statuses):
    """Translate Windows spooler status text into an actionable failure."""
    normalized = "".join(str(status or "").casefold().replace(" ", "") for status in statuses)
    for token, message in PRINTER_ERROR_TOKENS.items():
        if token in normalized:
            return message
    return None


def _status_names(flags, mapping):
    return ",".join(name for bit, name in mapping.items() if int(flags or 0) & bit)


def _windows_spooler_snapshot(printer_name):
    """Read printer/job state through Winspool without admin rights or pywin32.

    The result is deliberately small because it may be sampled repeatedly while
    one document is printing.  Failure to read the spooler is treated as lack of
    verification, never as proof that paper was produced.
    """
    if sys.platform != "win32":
        raise OSError("Windows 打印后台仅可在 Windows 中读取")

    class PrinterInfo6(ctypes.Structure):
        _fields_ = [("status", wintypes.DWORD)]

    class SystemTime(ctypes.Structure):
        _fields_ = [
            ("year", wintypes.WORD), ("month", wintypes.WORD),
            ("day_of_week", wintypes.WORD), ("day", wintypes.WORD),
            ("hour", wintypes.WORD), ("minute", wintypes.WORD),
            ("second", wintypes.WORD), ("milliseconds", wintypes.WORD),
        ]

    class JobInfo1(ctypes.Structure):
        _fields_ = [
            ("job_id", wintypes.DWORD),
            ("printer_name", wintypes.LPWSTR),
            ("machine_name", wintypes.LPWSTR),
            ("user_name", wintypes.LPWSTR),
            ("document", wintypes.LPWSTR),
            ("data_type", wintypes.LPWSTR),
            ("status_text", wintypes.LPWSTR),
            ("status", wintypes.DWORD),
            ("priority", wintypes.DWORD),
            ("position", wintypes.DWORD),
            ("total_pages", wintypes.DWORD),
            ("pages_printed", wintypes.DWORD),
            ("submitted", SystemTime),
        ]

    spooler = ctypes.WinDLL("winspool.drv", use_last_error=True)
    handle = wintypes.HANDLE()
    open_printer = spooler.OpenPrinterW
    open_printer.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    open_printer.restype = wintypes.BOOL
    close_printer = spooler.ClosePrinter
    close_printer.argtypes = [wintypes.HANDLE]
    close_printer.restype = wintypes.BOOL
    get_printer = spooler.GetPrinterW
    get_printer.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.BYTE),
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    get_printer.restype = wintypes.BOOL
    enum_jobs = spooler.EnumJobsW
    enum_jobs.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.BYTE), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ]
    enum_jobs.restype = wintypes.BOOL

    if not open_printer(str(printer_name), ctypes.byref(handle), None):
        raise OSError(ctypes.get_last_error(), f"无法打开 Windows 打印机：{printer_name}")
    try:
        needed = wintypes.DWORD()
        get_printer(handle, 6, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError(ctypes.get_last_error(), "无法读取 Windows 打印机状态")
        printer_buffer = (wintypes.BYTE * needed.value)()
        if not get_printer(handle, 6, printer_buffer, needed.value, ctypes.byref(needed)):
            raise OSError(ctypes.get_last_error(), "无法读取 Windows 打印机状态")
        printer_info = ctypes.cast(printer_buffer, ctypes.POINTER(PrinterInfo6)).contents

        jobs_needed, jobs_returned = wintypes.DWORD(), wintypes.DWORD()
        enum_jobs(handle, 0, 999, 1, None, 0, ctypes.byref(jobs_needed), ctypes.byref(jobs_returned))
        if not jobs_needed.value:
            jobs = {}
        else:
            jobs_buffer = (wintypes.BYTE * jobs_needed.value)()
            if not enum_jobs(
                handle, 0, 999, 1, jobs_buffer, jobs_needed.value,
                ctypes.byref(jobs_needed), ctypes.byref(jobs_returned),
            ):
                raise OSError(ctypes.get_last_error(), "无法读取 Windows 打印队列")
            records = ctypes.cast(jobs_buffer, ctypes.POINTER(JobInfo1))
            jobs = {
                int(records[index].job_id): {
                    "document": str(records[index].document or ""),
                    "status": ",".join(filter(None, [
                        str(records[index].status_text or ""),
                        _status_names(records[index].status, JOB_STATUS_NAMES),
                    ])),
                }
                for index in range(jobs_returned.value)
            }
        return {
            "printer_status": _status_names(printer_info.status, PRINTER_STATUS_NAMES),
            "jobs": jobs,
        }
    finally:
        close_printer(handle)


def _wait_for_windows_spooler(printer_name, original_job_ids):
    """Wait until the newly submitted job leaves the Windows spooler.

    A disappeared, previously observed job means Windows/driver completed its
    queue processing.  It is stronger than a successful Sumatra exit, although
    no ordinary driver API can prove that a physical sheet reached the output
    tray.  If the job cannot be observed, return a conservative pending state.
    """
    deadline = time.monotonic() + VERIFY_SECONDS
    discovery_deadline = time.monotonic() + min(12, VERIFY_SECONDS)
    observed_job_ids = set()
    while time.monotonic() < deadline:
        try:
            snapshot = _windows_spooler_snapshot(printer_name)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            return "submitted_only"
        problem = _printer_problem(snapshot["printer_status"])
        if problem:
            raise RuntimeError(problem)
        jobs = snapshot["jobs"]
        observed_job_ids.update(set(jobs) - set(original_job_ids))
        for job_id in observed_job_ids & set(jobs):
            problem = _printer_problem(jobs[job_id]["status"])
            if problem:
                raise RuntimeError(problem)
            normalized = jobs[job_id]["status"].casefold().replace(" ", "")
            if "complete" in normalized or "printed" in normalized:
                return "spooler_completed"
        if observed_job_ids and not (observed_job_ids & set(jobs)):
            return "spooler_completed"
        if not observed_job_ids and time.monotonic() >= discovery_deadline:
            return "submitted_only"
        time.sleep(1)
    if observed_job_ids:
        raise RuntimeError(f"打印任务在 Windows 队列中超过 {VERIFY_SECONDS} 秒未完成，请检查缺纸、卡纸或离线状态")
    return "submitted_only"


def submit_pdf_to_printer(pdf_path):
    """Submit one PDF and return the strongest available completion evidence."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file() or pdf_path.open("rb").read(5) != b"%PDF-":
        raise RuntimeError("试打印文件不存在或不是 PDF")
    configured_command = print_command()
    executable = Path(configured_command[0])
    if sys.platform == "win32" and executable.name.casefold() == "sumatrapdf.exe":
        if not any((executable.parent / name).is_file() for name in ("libmupdf.dll", "libsumatrapdf.dll")):
            raise RuntimeError("所选 SumatraPDF.exe 缺少配套打印引擎 DLL，可能是改名后的安装包；请改用安装目录中的程序")
    command = [part.replace("{file}", str(pdf_path)) for part in configured_command]
    printer_name = _configured_printer_name(configured_command) if sys.platform == "win32" else None
    original_job_ids = set()
    if printer_name:
        try:
            before = _windows_spooler_snapshot(printer_name)
            problem = _printer_problem(before["printer_status"])
            if problem:
                raise RuntimeError(problem)
            original_job_ids = set(before["jobs"])
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            printer_name = None
    try:
        subprocess.run(command, check=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        reason = {
            2: "PDF 无法打开", 3: "PDF 禁止打印", 4: "找不到指定打印机",
            5: "打印机驱动或设备出错", 6: "被系统策略禁止打印",
        }.get(exc.returncode, "请检查打印机及打印程序")
        raise RuntimeError(f"提交打印失败（代码 {exc.returncode}）：{reason}") from exc
    if printer_name:
        return _wait_for_windows_spooler(printer_name, original_job_ids)
    return "submitted_only"


def request(path, *, method="GET", data=None, lease_token=None):
    headers = {"X-Print-Agent-Token": TOKEN, "User-Agent": USER_AGENT}
    if lease_token:
        headers["X-Print-Lease-Token"] = lease_token
    url = BASE_URL + path
    try:
        response = curl_requests.request(
            method,
            url,
            headers=headers,
            json=data,
            timeout=30,
            impersonate="chrome",
        )
    except curl_requests.RequestsError as exc:
        raise URLError(f"HTTPS 连接失败：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPError(url, response.status_code, response.reason, response.headers, None)
    content = response.content
    return content if path.endswith("/pdf") else response.json().get("data")


def print_job(job):
    pdf_bytes = request(job["pdf_path"], lease_token=job["lease_token"])
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("服务器没有返回 PDF")
    with tempfile.TemporaryDirectory(prefix="yecai-print-") as folder:
        pdf_path = Path(folder) / f"placement-{job['order_id']}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        return submit_pdf_to_printer(pdf_path)


def run_once(*, recover_failed=False):
    job = request(
        "/api/printing/agent/jobs/claim",
        method="POST",
        data={"recover_failed": bool(recover_failed)},
    )
    if not job:
        return None
    try:
        verification = print_job(job)
        result = {
            "lease_token": job["lease_token"],
            "success": True,
            "verification": verification,
        }
    except Exception as exc:
        result = {"lease_token": job["lease_token"], "success": False, "error": str(exc)[:500]}
    request(f"/api/printing/agent/jobs/{job['id']}/complete", method="POST", data=result)
    if result["success"] and result.get("verification") == "spooler_completed":
        print(f"job {job['id']}: Windows 打印队列已完成", flush=True)
        return True
    if result["success"]:
        print(f"job {job['id']}: 已提交但无法确认实际出纸，请在系统中确认", flush=True)
        return False
    print(f"job {job['id']}: {result['error']}", flush=True)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="置货单本地自动打印客户端")
    parser.add_argument("--test-print", metavar="PDF", help="只打印本地 PDF，不连接服务器或使用认证码")
    parser.add_argument("--once", action="store_true", help="只领取一次任务，便于本地调试")
    args = parser.parse_args(argv)
    try:
        print_command()
        if args.test_print:
            verification = submit_pdf_to_printer(args.test_print)
            if verification == "spooler_completed":
                print("Windows 打印队列已完成；请目视确认实际出纸和版式。", flush=True)
            else:
                print("PDF 已提交，但驱动未提供可靠的完成状态；请检查实际出纸。", flush=True)
            return
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc
    if not server_url_is_safe(BASE_URL):
        raise SystemExit("YECAI_SERVER_URL 必须为 HTTPS；仅本机 127.0.0.1/::1 可使用 HTTP")
    if not TOKEN:
        raise SystemExit("请设置 YECAI_PRINT_AGENT_TOKEN")
    if args.once:
        try:
            if run_once(recover_failed=True) is None:
                print("当前没有待自动打印的置货单。", flush=True)
        except HTTPError as exc:
            raise SystemExit(f"打印接口错误 HTTP {exc.code}；请检查设备认证码和服务器地址") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SystemExit(f"打印服务暂不可用：{exc}") from exc
        return
    recover_failed = True
    while True:
        try:
            # 启动后先连续清空历史积压，再进入轮询；打印仍按任务顺序提交，避免并发冲击打印机。
            while run_once(recover_failed=recover_failed) is True:
                recover_failed = False
            recover_failed = False
        except HTTPError as exc:
            print(f"打印接口错误 HTTP {exc.code}", file=sys.stderr, flush=True)
            if exc.code in {401, 403}:
                raise SystemExit("设备认证失败，请联系系统管理员检查认证码") from exc
        except (URLError, TimeoutError, OSError) as exc:
            print(f"打印服务暂不可用：{exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
