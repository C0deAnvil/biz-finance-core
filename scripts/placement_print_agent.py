"""Poll and silently print placement PDFs on a trusted printer computer.

Configuration is supplied through environment variables; the device token is
never written to a file or command-line argument by this program.
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("YECAI_SERVER_URL", "").rstrip("/")
TOKEN = os.environ.get("YECAI_PRINT_AGENT_TOKEN", "")
PRINT_COMMAND = json.loads(os.environ.get("YECAI_PRINT_COMMAND_JSON", "[]"))
POLL_SECONDS = max(30, int(os.environ.get("YECAI_PRINT_POLL_SECONDS", "60")))
USER_AGENT = f"YecaiPlacementPrintAgent/1.0 ({platform.node()[:60]})"


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
        command = [part.replace("{file}", str(pdf_path)) for part in PRINT_COMMAND]
        if not any("{file}" in part for part in PRINT_COMMAND):
            raise RuntimeError("打印命令必须包含 {file} 占位符")
        subprocess.run(command, check=True, timeout=180)


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
    if not BASE_URL.startswith("https://"):
        raise SystemExit("YECAI_SERVER_URL 必须为 HTTPS 地址")
    if not TOKEN or not PRINT_COMMAND or not isinstance(PRINT_COMMAND, list):
        raise SystemExit("请设置 YECAI_PRINT_AGENT_TOKEN 和 YECAI_PRINT_COMMAND_JSON")
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
