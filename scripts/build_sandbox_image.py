"""Build the Docker script-sandbox image (lx.sandbox.IMAGE) from pinned inputs.

The Pillow wheel is the Linux wheel pinned in uv.lock; it is downloaded once
into .lx/sandbox-image/ and checked against the lock's SHA-256 before use.
The base image is the digest-pinned python:3.12-slim already used by the core.
"""
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from lx.sandbox import IMAGE  # noqa: E402

WHEEL = "pillow-11.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"


def locked_wheel():
    lock = (ROOT/"uv.lock").read_text(encoding="utf-8")
    match = re.search(r'url = "(https://files\.pythonhosted\.org/[^"]*/' + re.escape(WHEEL)
                      + r')", hash = "sha256:([0-9a-f]{64})"', lock)
    if not match:
        raise SystemExit(f"{WHEEL} is not pinned in uv.lock")
    return match.group(1), match.group(2)


def main():
    url, digest = locked_wheel()
    context = ROOT/".lx"/"sandbox-image"
    context.mkdir(parents=True, exist_ok=True)
    wheel = context/WHEEL
    if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
        print(f"Downloading {WHEEL} from PyPI")
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit("downloaded wheel does not match the uv.lock hash")
        wheel.write_bytes(data)
    shutil.copy2(ROOT/"docker"/"sandbox.Dockerfile", context/"Dockerfile")
    subprocess.run(["docker", "build", "--tag", IMAGE, str(context)], check=True)
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
                              capture_output=True, text=True, check=True).stdout.strip()
    print(f"{IMAGE} {image_id}")


if __name__ == "__main__":
    main()
