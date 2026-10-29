"""Run improver-written helper scripts in a throwaway Linux sandbox.

Harness scripts are code the improver wrote, so they run isolated (WSL/Linux
controller only): a new user, mount and network namespace (`unshare -rnm`),
a chroot whose only contents are read-only binds of /usr, /lib, /lib64, /bin,
the venv's site-packages (for Pillow) and a private writable work directory,
no network, a minimal environment, CPU/memory/file limits and a wall-clock
timeout that kills the whole process group. The work directory holds copies
of the inputs and is deleted afterwards; nothing else on the host is visible.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

TIMEOUT = 20
MAX_SOURCE = 20_000
MAX_INPUT = 2_000_000
MAX_STDOUT = 20_000
MAX_STDERR = 2_000
MEMORY = 2 * 1024 ** 3
FILE_SIZE = 50 * 1024 ** 2

_SETUP = r'''
set -e
R=$1; SITE=$2; W=$3; PY=$4; CHROOT=$5
for d in usr lib lib64 bin; do
  [ -e /$d ] || continue
  if [ -L /$d ]; then ln -s "$(readlink /$d)" $R/$d; continue; fi
  mkdir -p $R/$d; mount --rbind /$d $R/$d; mount -o remount,bind,ro $R/$d
done
mkdir -p $R/site $R/work $R/tmp $R/dev
mount --bind "$SITE" $R/site; mount -o remount,bind,ro $R/site
mount --bind "$W" $R/work
for n in null zero urandom; do touch $R/dev/$n; mount --bind /dev/$n $R/dev/$n; done
cd /
exec "$CHROOT" $R /usr/bin/env -i PATH=/usr/bin:/bin HOME=/work LANG=C.UTF-8 PYTHONPATH=/site \
  PYTHONDONTWRITEBYTECODE=1 $PY -s -B /work/script.py
'''


def available():
    """True on a Linux controller with unshare and chroot (the WSL controller)."""
    return sys.platform == "linux" and all(shutil.which(tool) for tool in ("unshare", "chroot", "sh"))


def _limits(cpu):
    import resource

    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE, FILE_SIZE))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    return apply


def run_python(source, stdin_text, files=None, *, timeout=TIMEOUT):
    """Run `source` with `stdin_text` on stdin and `files` ({relative path: bytes}) in its work dir.

    Returns {"exit_code", "stdout", "stderr", "timed_out"}; stdout and stderr
    are truncated. Raises ValueError for bad inputs and RuntimeError when no
    sandbox is available (never runs the code unsandboxed).
    """
    if not available():
        raise RuntimeError("script sandbox unavailable: needs the Linux controller with unshare and chroot")
    if type(source) is not str or not source.strip() or len(source) > MAX_SOURCE:
        raise ValueError(f"script source must be non-empty and at most {MAX_SOURCE} characters")
    if type(stdin_text) is not str or len(stdin_text) > MAX_INPUT:
        raise ValueError("script input too large")
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("timeout must be 1-60 seconds")
    python = f"/usr/bin/python{sys.version_info.major}.{sys.version_info.minor}"
    site = sysconfig.get_paths()["purelib"]
    base = Path(tempfile.mkdtemp(prefix="lx-sandbox-"))
    try:
        root, work = base / "root", base / "work"
        root.mkdir()
        work.mkdir()
        (work / "script.py").write_text(source, encoding="utf-8")
        for rel, data in (files or {}).items():
            target = (work / rel).resolve()
            if not target.is_relative_to(work.resolve()) or rel.startswith("script.py"):
                raise ValueError(f"bad sandbox file path {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        process = subprocess.Popen(
            ["unshare", "-rnm", "--propagation", "private", "sh", "-c", _SETUP, "sh",
             str(root), site, str(work), python, shutil.which("chroot")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}, cwd=str(base), start_new_session=True, preexec_fn=_limits(timeout))
        timed_out = False
        try:
            stdout, stderr = process.communicate(stdin_text.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return {"exit_code": process.returncode, "timed_out": timed_out,
                "stdout": stdout[:MAX_STDOUT].decode("utf-8", "replace"),
                "stderr": stderr[-MAX_STDERR:].decode("utf-8", "replace")}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_json(source, payload, files=None, *, timeout=TIMEOUT):
    """run_python with a JSON payload; parses stdout as JSON when it is JSON."""
    result = run_python(source, json.dumps(payload, allow_nan=False), files, timeout=timeout)
    try:
        result["output"] = json.loads(result["stdout"]) if result["stdout"].strip() else None
    except ValueError:
        result["output"] = result["stdout"]
    del result["stdout"]
    return result
