"""一键运行测试: venv 内执行所有 pytest 用例。

用法(项目根目录):
    python scripts/run_tests.py
    python scripts/run_tests.py --fast   # 跳过并发测试
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="运行 yecai ERP 测试套件")
    parser.add_argument("--fast", action="store_true", help="跳过并发测试(需数据库锁,CI 外推荐)")
    args = parser.parse_args()

    python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = PROJECT_ROOT / "venv" / "bin" / "python"
    if not python.exists():
        print("未找到 venv,请先创建虚拟环境")
        sys.exit(1)

    tests = ["tests/test_smoke.py", "tests/test_inventory_api.py"]
    if not args.fast:
        tests.append("tests/test_concurrency.py")

    cmd = [str(python), "-m", "pytest", *tests, "-v"]
    print("▶", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()