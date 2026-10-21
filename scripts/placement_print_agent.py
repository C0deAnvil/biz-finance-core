"""Poll and silently print placement PDFs on a trusted printer computer.

Configuration is supplied through environment variables; the device token is
never written to a file or command-line argument by this program.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("YECAI_SERVER_URL", "").rstrip("/")
TOKEN = os.environ.get("YECAI_PRINT_AGENT_TOKEN", "")
POLL_SECONDS = max(30, int(os.environ.get("YECAI_PRINT_POLL_SECONDS", "60")))
USER_AGENT = f"YecaiPlacementPrintAgent/1.0 ({platform.node()[:60]})"


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


def submit_pdf_to_printer(pdf_path):
    """Submit a PDF to the configured local printer command."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file() or pdf_path.open("rb").read(5) != b"%PDF-":
        raise RuntimeError("试打印文件不存在或不是 PDF")
    configured_command = print_command()
    executable = Path(configured_command[0])
    if sys.platform == "win32" and executable.name.casefold() == "sumatrapdf.exe":
        if not any((executable.parent / name).is_file() for name in ("libmupdf.dll", "libsumatrapdf.dll")):
            raise RuntimeError("所选 SumatraPDF.exe 缺少配套打印引擎 DLL，可能是改名后的安装包；请改用安装目录中的程序")
    command = [part.replace("{file}", str(pdf_path)) for part in configured_command]
    try:
        subprocess.run(command, check=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        reason = {
            2: "PDF 无法打开", 3: "PDF 禁止打印", 4: "找不到指定打印机",
            5: "打印机驱动或设备出错", 6: "被系统策略禁止打印",
        }.get(exc.returncode, "请检查打印机及打印程序")
        raise RuntimeError(f"提交打印失败（代码 {exc.returncode}）：{reason}") from exc


def request(path, *, method="GET", data=None, lease_token=None):
    headers = {"X-Print-Agent-Token": TOKEN, "User-Agent": USER_AGENT}
    if lease_token:
        headers["X-Print-Lease-Token"] = lease_token
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(BASE_URL + path, data=body, headers=headers, method=method)
    with urlopen(req, timeout=30) as response:
        content = response.read()
        return content if path.endswith("/pdf") else json.loads(content).get("data")


def print_job(job):
    pdf_bytes = request(job["pdf_path"], lease_token=job["lease_token"])
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("服务器没有返回 PDF")
    with tempfile.TemporaryDirectory(prefix="yecai-print-") as folder:
        pdf_path = Path(folder) / f"placement-{job['order_id']}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        submit_pdf_to_printer(pdf_path)


def run_once():
    job = request("/api/printing/agent/jobs/claim", method="POST", data={})
    if not job:
        return False
    try:
        print_job(job)
        result = {"lease_token": job["lease_token"], "success": True}
    except Exception as exc:
        result = {"lease_token": job["lease_token"], "success": False, "error": str(exc)[:500]}
    request(f"/api/printing/agent/jobs/{job['id']}/complete", method="POST", data=result)
    print(f"job {job['id']}: {'submitted' if result['success'] else result['error']}", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description="置货单本地自动打印客户端")
    parser.add_argument("--test-print", metavar="PDF", help="只打印本地 PDF，不连接服务器或使用认证码")
    parser.add_argument("--once", action="store_true", help="只领取一次任务，便于本地调试")
    args = parser.parse_args()
    try:
        print_command()
        if args.test_print:
            submit_pdf_to_printer(args.test_print)
            print("PDF 打印命令已正常完成；请检查 Windows 打印队列和实际出纸。", flush=True)
            return
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc
    if not server_url_is_safe(BASE_URL):
        raise SystemExit("YECAI_SERVER_URL 必须为 HTTPS；仅本机 127.0.0.1/::1 可使用 HTTP")
    if not TOKEN:
        raise SystemExit("请设置 YECAI_PRINT_AGENT_TOKEN")
    if args.once:
        try:
            if not run_once():
                print("当前没有待自动打印的置货单。", flush=True)
        except HTTPError as exc:
            raise SystemExit(f"打印接口错误 HTTP {exc.code}；请检查设备认证码和服务器地址") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SystemExit(f"打印服务暂不可用：{exc}") from exc
        return
    while True:
        try:
            run_once()
        except HTTPError as exc:
            print(f"打印接口错误 HTTP {exc.code}", file=sys.stderr, flush=True)
            if exc.code in {401, 403}:
                raise SystemExit("设备认证失败，请联系系统管理员检查认证码") from exc
        except (URLError, TimeoutError, OSError) as exc:
            print(f"打印服务暂不可用：{exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
