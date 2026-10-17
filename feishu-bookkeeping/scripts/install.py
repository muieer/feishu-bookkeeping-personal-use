#!/usr/bin/env python3
"""Install into an explicit local skill directory, or prepare this bundle in place."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def choose_python(explicit=None):
    candidates = (
        [explicit]
        if explicit
        else [sys.executable]
        + [
            shutil.which(name)
            for name in ("python3.12", "python3.13", "python3.14", "python3.11", "python3")
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        check = subprocess.run(
            [candidate, "-c", "import sys;print(int(sys.version_info >= (3,11)))"],
            capture_output=True,
            text=True,
        )
        if check.returncode == 0 and check.stdout.strip() == "1":
            return str(Path(candidate).absolute())
    raise ValueError("未找到 Python 3.11+；安装后用 --python 指定解释器")


def install(destination=None, python=None, offline=False):
    source = Path(__file__).resolve().parents[1]
    if (source / ".evaluation-only").exists():
        raise ValueError("隔离验收副本不能安装到生产")
    if not (source / "private/config.json").is_file():
        raise ValueError("缺少私有账本配置，不能猜测目标表")
    executable = choose_python(python)
    target = Path(destination).expanduser().resolve() if destination else source
    if target != source:
        if target.exists():
            raise ValueError("目标目录已存在，停止安装以保留现有版本")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", ".runtime", ".DS_Store"),
        )
    for root, _dirs, files in os.walk(target / "private"):
        Path(root).chmod(0o700)
        for name in files:
            (Path(root) / name).chmod(0o600)
    environment = target / ".venv"
    if not environment.exists():
        subprocess.run([executable, "-m", "venv", str(environment)], check=True)
    interpreter = environment / "bin/python"
    args = [
        str(interpreter),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "-r",
        str(target / "requirements.txt"),
    ]
    if (target / "wheels").is_dir():
        args += ["--find-links", str(target / "wheels")]
    if offline:
        args += ["--no-index"]
    subprocess.run(args, check=True)
    check = subprocess.run(
        [str(interpreter), str(target / "scripts/bookkeeping.py")],
        input=json.dumps({"tool": "doctor", "args": {"live": False}}),
        text=True,
        capture_output=True,
    )
    if check.returncode:
        raise ValueError("本地配置/知识检查失败；安装没有执行账本写入")
    return {
        "installed": str(target),
        "python": str(interpreter),
        "local_check": json.loads(check.stdout),
        "live_write_count": 0,
    }


def main():
    parser = argparse.ArgumentParser(description="安装或准备 feishu-bookkeeping 本地 Skill")
    parser.add_argument("--destination", help="宿主的完整技能目录；省略则在当前包建立独立环境")
    parser.add_argument("--python", help="Python 3.11+ 可执行文件")
    parser.add_argument("--offline", action="store_true", help="只用包内 wheels，不联网")
    args = parser.parse_args()
    try:
        result = install(args.destination, args.python, args.offline)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
