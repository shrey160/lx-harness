"""Run improver-written helper scripts in a throwaway sandbox.

Harness scripts are code the improver wrote, so they always run isolated,
never on the host. Two backends:

Linux controller (WSL/Linux): a new user, mount and network namespace (`unshare -rnm`),
a chroot whose only contents are read-only binds of /usr, /lib, /lib64, /bin,
the venv's site-packages (for Pillow) and a private writable work directory,
no network, a minimal environment, CPU/memory/file limits and a wall-clock
timeout that kills the whole process group. The work directory holds copies
of the inputs and is deleted afterwards; nothing else on the host is visible.

Docker (native Windows, or Linux without unshare; D-016): the core's
DockerRunner starts a disposable container from IMAGE (built by
scripts/build_sandbox_image.py) with no network, a read-only root, no
capabilities, an unprivileged user, memory/CPU/PID limits and only the
same throwaway work directory mounted at /work.
"""
from __future__ import annotations

import functools
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
IMAGE = "lx-sandbox:pillow-11.3.0"
DOCKER_STARTUP = 10  # container create/start/stop overhead on top of the script's own timeout
_DOCKER_MAIN = ("import runpy, sys; sys.stdin = open('/work/.stdin', encoding='utf-8'); "
                "runpy.run_path('/work/script.py', run_name='__main__')")

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


def _linux():
    return sys.platform == "linux" and all(shutil.which(tool) for tool in ("unshare", "chroot", "sh"))


@functools.lru_cache(maxsize=None)
def _docker_image():
    """Immutable ID of the local sandbox image, or None when Docker or the image is missing."""
    if not shutil.which("docker"):
        return None
    try:
        from h2portable.execution import resolve_image
        return resolve_image(IMAGE)
    except (ValueError, OSError):
        return None


def backend():
    """"linux-namespace", "docker" or None (scripts must then stay off)."""
    if _linux():
        return "linux-namespace"
    return "docker" if _docker_image() else None


def available():
    """True when helper scripts can run isolated on this controller."""
    return backend() is not None


def _limits(cpu):
    import resource

    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE, FILE_SIZE))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    return apply


def run_python(source, stdin_text, files=None, *, timeout=TIMEOUT, max_stdout=MAX_STDOUT):
    """Run `source` with `stdin_text` on stdin and `files` ({relative path: bytes}) in its work dir.

    Returns {"exit_code", "stdout", "stderr", "timed_out"}; stdout and stderr
    are truncated. Raises ValueError for bad inputs and RuntimeError when no
    sandbox is available (never runs the code unsandboxed).
    """
    kind = backend()
    if kind is None:
        raise RuntimeError("script sandbox unavailable: needs the Linux controller (unshare, chroot) or Docker "
                           f"with the {IMAGE} image (scripts/build_sandbox_image.py)")
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
        if kind == "docker":
            return _run_docker(base, work, stdin_text, timeout, max_stdout)
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
                "stdout": stdout[:max_stdout].decode("utf-8", "replace"),
                "stderr": stderr[-MAX_STDERR:].decode("utf-8", "replace")}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _run_docker(base, work, stdin_text, timeout, max_stdout=MAX_STDOUT):
    from h2portable.execution import DockerRunner
    (work / ".stdin").write_text(stdin_text, encoding="utf-8")
    logs = base / "logs"
    result = DockerRunner(_docker_image(), terminate_grace=1.0).run(
        ["python", "-s", "-B", "-c", _DOCKER_MAIN], work, logs, timeout + DOCKER_STARTUP)
    if result.outcome == "failed" and result.returncode is None:
        raise RuntimeError(f"Docker script sandbox failed: {result.error or 'no exit code'}")
    stdout, stderr = Path(result.stdout_file).read_bytes(), Path(result.stderr_file).read_bytes()
    return {"exit_code": result.returncode, "timed_out": result.outcome == "timed_out",
            "stdout": stdout[:max_stdout].decode("utf-8", "replace"),
            "stderr": stderr[-MAX_STDERR:].decode("utf-8", "replace")}


def run_json(source, payload, files=None, *, timeout=TIMEOUT, max_stdout=MAX_STDOUT):
    """run_python with a JSON payload; parses stdout as JSON when it is JSON."""
    result = run_python(source, json.dumps(payload, allow_nan=False), files, timeout=timeout, max_stdout=max_stdout)
    try:
        result["output"] = json.loads(result["stdout"]) if result["stdout"].strip() else None
    except ValueError:
        result["output"] = result["stdout"]
    del result["stdout"]
    return result
