# Script sandbox image for controllers without the Linux namespace sandbox (native Windows, D-016).
# Built offline from the digest-pinned base already used by the core and the Pillow wheel pinned in
# uv.lock (scripts/build_sandbox_image.py downloads and hash-checks it). Containers run with no
# network, a read-only root, no capabilities and an unprivileged user (lx.sandbox).
ARG PYTHON_IMAGE=python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY pillow-11.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl /tmp/
RUN python -m pip install --no-cache-dir --no-index --no-deps /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && rm -rf /home /mnt /media /srv /opt
USER 65534:65534
WORKDIR /work
CMD ["python", "--version"]
