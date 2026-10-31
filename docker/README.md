# docker

`sandbox.Dockerfile` defines the image that runs scoring checks and helper scripts on
Windows, `lx-sandbox:pillow-11.3.0`. It uses a digest-pinned base image and the Pillow
wheel pinned in `uv.lock`.

Build it with `scripts/build_sandbox_image.py`, which assembles the build context under
`.lx/sandbox-image/`. Containers run with no network, a read-only root, no capabilities
and an unprivileged user (`lx.sandbox`).

To add a library or a system tool, read [Preparing an environment](../docs/environment.md).
