"""Safe, deterministic snapshots of small sample-data folders and ZIP files."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath


SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".txt", ".json", ".csv"}
MAX_FILES = 100
MAX_BYTES = 20 * 1024 * 1024
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_ZIP_RATIO = 1000
_SECRET_TOKENS = {
    "credential", "credentials", "secret", "secrets", "private", "key",
    "token", "password", "passphrase", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "id_ed25519_sk", "privatekey", "privatekeys", "rsa",
    "dsa", "ecdsa", "ed25519", "keystore",
}


class IntakeError(ValueError):
    """The source cannot be snapshotted under the intake contract."""


def _kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "image"
    if suffix in {".txt", ".json"}:
        return "text"
    return "table"


def _check_name(name: str) -> None:
    """Reject protected names without opening the corresponding file."""
    leaf = name.rsplit("/", 1)[-1]
    lower = leaf.casefold()
    if lower.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "service-account", "service_account")):
        raise IntakeError(f"protected filename: {name}")
    if leaf.startswith(".") or lower == ".env" or lower.endswith(".env") or ".env." in lower:
        raise IntakeError(f"protected filename: {name}")
    if lower.startswith(".env."):
        raise IntakeError(f"protected filename: {name}")
    tokens = set(re.findall(r"[a-z0-9]+", lower))
    if tokens & _SECRET_TOKENS or lower.endswith((".pem", ".key", ".p12", ".pfx")):
        raise IntakeError(f"protected filename: {name}")


def _check_component(component: str) -> None:
    if "\\" in component or ":" in component or component.endswith((" ", ".")):
        raise IntakeError(f"non-portable filename: {component}")


def _is_link_mode(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0) or 0
    return bool(reparse and attributes & reparse)


def _paths_disjoint(source: Path, destination: Path) -> tuple[Path, Path]:
    try:
        src = source.resolve(strict=True)
    except OSError as exc:
        raise IntakeError("source does not exist or cannot be resolved") from exc
    dst = destination.resolve(strict=False)
    if src == dst or src in dst.parents or dst in src.parents:
        raise IntakeError("source and destination must not contain one another")
    return src, dst


def _check_destination(dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        raise IntakeError("destination already exists")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IntakeError("destination parent cannot be created") from exc


def _walk_folder(source: Path) -> list[tuple[Path, str, int]]:
    entries: list[tuple[Path, str, int]] = []
    input_bytes = 0
    input_count = 0

    def visit(directory: Path, prefix: str) -> None:
        nonlocal input_bytes, input_count
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        except OSError as exc:
            raise IntakeError(f"cannot inspect source directory: {prefix or '.'}") from exc
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            _check_component(child.name)
            _check_name(relative)
            try:
                info = child.lstat()
            except OSError as exc:
                raise IntakeError(f"cannot inspect source entry: {relative}") from exc
            if _is_link_mode(info):
                raise IntakeError(f"links are not allowed: {relative}")
            if stat.S_ISDIR(info.st_mode):
                visit(child, relative)
            elif stat.S_ISREG(info.st_mode):
                input_count += 1
                input_bytes += info.st_size
                if input_count > MAX_FILES:
                    raise IntakeError(f"sample exceeds {MAX_FILES} files")
                if input_bytes > MAX_BYTES:
                    raise IntakeError(f"sample exceeds {MAX_BYTES} bytes")
                if child.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                entries.append((child, relative, info.st_size))
            else:
                raise IntakeError(f"non-regular entry is not allowed: {relative}")

    visit(source, "")
    return entries


def _copy_and_hash(source: Path, destination: Path, expected_size: int) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as src:
            info = os.fstat(src.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                raise IntakeError(f"source entry changed during snapshot: {source.name}")
            digest = hashlib.sha256()
            copied = 0
            with destination.open("wb") as dst:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > expected_size or copied > MAX_BYTES:
                        raise IntakeError(f"source entry changed during snapshot: {source.name}")
                    digest.update(block)
                    dst.write(block)
            if copied != expected_size:
                raise IntakeError(f"source entry changed during snapshot: {source.name}")
            return digest.hexdigest()
    except IntakeError:
        raise
    except OSError as exc:
        raise IntakeError(f"cannot read supported sample file: {source.name}") from exc


def _inventory_record(path: str, size: int, digest: str) -> dict[str, object]:
    return {"path": path, "size": size, "sha256": digest, "kind": _kind(path)}


def _new_stage(destination: Path) -> Path:
    try:
        return Path(tempfile.mkdtemp(prefix=f".{destination.name}.snapshot-", dir=destination.parent))
    except OSError as exc:
        raise IntakeError("cannot create temporary snapshot directory") from exc


def _publish(stage: Path, destination: Path) -> None:
    if destination.exists():
        raise IntakeError("destination already exists")
    try:
        os.replace(stage, destination)
    except OSError as exc:
        raise IntakeError("cannot publish snapshot destination") from exc


def _cleanup_stage(stage: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)


def snapshot_folder(source: Path, destination: Path) -> dict[str, object]:
    """Copy supported regular files into a new atomic snapshot directory."""
    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
        raise IntakeError("source root cannot be a link")
    if destination.is_symlink() or (hasattr(destination, "is_junction") and destination.is_junction()):
        raise IntakeError("destination already exists")
    src, dst = _paths_disjoint(source, destination)
    if not src.is_dir():
        raise IntakeError("source must be a directory")
    _check_destination(dst)
    entries = _walk_folder(src)  # validate every name and type before opening content
    stage = _new_stage(dst)
    inventory: list[dict[str, object]] = []
    try:
        total_bytes = 0
        for file_path, relative, expected_size in entries:
            digest = _copy_and_hash(file_path, stage / Path(*PurePosixPath(relative).parts), expected_size)
            inventory.append(_inventory_record(relative, expected_size, digest))
            total_bytes += expected_size
        _publish(stage, dst)
    except BaseException:
        _cleanup_stage(stage)
        raise
    return {"source_name": src.name, "inventory": inventory, "total_bytes": total_bytes}


def _zip_relative_name(info: zipfile.ZipInfo) -> str:
    raw = info.filename
    if not raw or "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise IntakeError(f"unsafe ZIP path: {raw!r}")
    trimmed = raw[:-1] if info.is_dir() and raw.endswith("/") else raw
    parts = trimmed.split("/")
    if not trimmed or any(part in {"", ".", ".."} for part in parts) or ":" in trimmed:
        raise IntakeError(f"unsafe ZIP path: {raw!r}")
    if len(trimmed) > 4096 or any(len(part) > 255 for part in parts):
        raise IntakeError("ZIP path is too long")
    for index in range(1, len(parts) + 1):
        _check_name("/".join(parts[:index]))
    for part in parts:
        _check_component(part)
    return "/".join(parts)


def _zip_entries(data: bytes, archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    if not isinstance(data, bytes):
        raise IntakeError("ZIP input must be bytes")
    if len(data) > MAX_ZIP_BYTES:
        raise IntakeError(f"ZIP input exceeds {MAX_ZIP_BYTES} bytes")
    infos = archive.infolist()
    entries: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    total_size = 0
    count = 0
    for info in infos:
        relative = _zip_relative_name(info)
        key = relative.casefold()
        if key in seen:
            raise IntakeError(f"duplicate ZIP path: {relative}")
        seen.add(key)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise IntakeError(f"ZIP links are not allowed: {relative}")
        if info.is_dir():
            if file_type not in {0, stat.S_IFDIR}:
                raise IntakeError(f"invalid ZIP directory entry: {relative}")
            continue
        if file_type not in {0, stat.S_IFREG}:
            raise IntakeError(f"non-regular ZIP entry is not allowed: {relative}")
        if info.file_size < 0 or info.compress_size < 0:
            raise IntakeError(f"invalid ZIP size: {relative}")
        if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_ZIP_RATIO):
            raise IntakeError(f"ZIP compression ratio exceeds {MAX_ZIP_RATIO}: {relative}")
        total_size += info.file_size
        if total_size > MAX_BYTES:
            raise IntakeError(f"ZIP sample exceeds {MAX_BYTES} uncompressed bytes")
        count += 1
        if count > MAX_FILES:
            raise IntakeError(f"ZIP sample exceeds {MAX_FILES} files")
        if Path(relative).suffix.lower() in SUPPORTED_SUFFIXES:
            entries.append((info, relative))
    return entries


def snapshot_zip(data: bytes, destination: Path) -> dict[str, object]:
    """Validate then atomically extract supported files from an in-memory ZIP."""
    if not isinstance(data, bytes):
        raise IntakeError("ZIP input must be bytes")
    if len(data) > MAX_ZIP_BYTES:
        raise IntakeError(f"ZIP input exceeds {MAX_ZIP_BYTES} bytes")
    destination = Path(destination)
    if destination.is_symlink() or (hasattr(destination, "is_junction") and destination.is_junction()):
        raise IntakeError("destination already exists")
    dst = destination.resolve(strict=False)
    _check_destination(dst)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise IntakeError("input is not a valid ZIP archive") from exc
    with archive:
        entries = _zip_entries(data, archive)  # validate the full directory before decompressing
        files = [relative for _, relative in entries]
        roots = {name.split("/", 1)[0] for name in files}
        has_top_level_files = any("/" not in name for name in files)
        strip_root = len(roots) == 1 and not has_top_level_files and any("/" in name for name in files)
        root_name = next(iter(roots)) if strip_root else None
        stage = _new_stage(dst)
        inventory: list[dict[str, object]] = []
        total_bytes = 0
        try:
            for info, original in entries:
                relative = original[len(root_name) + 1 :] if root_name else original
                if not relative:
                    continue
                target = stage / Path(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                copied = 0
                try:
                    with archive.open(info, "r") as src, target.open("wb") as out:
                        while True:
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            copied += len(block)
                            if copied > info.file_size or total_bytes + copied > MAX_BYTES:
                                raise IntakeError("ZIP data exceeded declared size limits")
                            digest.update(block)
                            out.write(block)
                except (
                    OSError, ValueError, EOFError, RuntimeError, NotImplementedError,
                    zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error,
                ) as exc:
                    raise IntakeError(f"cannot safely extract ZIP member: {original}") from exc
                if copied != info.file_size:
                    raise IntakeError(f"ZIP member size mismatch: {original}")
                total_bytes += copied
                inventory.append(_inventory_record(relative, copied, digest.hexdigest()))
            _publish(stage, dst)
        except BaseException:
            _cleanup_stage(stage)
            raise
    return {"source_name": "data.zip", "inventory": inventory, "total_bytes": total_bytes}


def windows_to_wsl(path: str) -> str:
    """Translate a Windows drive path to its deterministic WSL mount path."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty Windows drive path")
    if path.startswith(("\\\\", "//")):
        raise ValueError("UNC paths are not supported")
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not match:
        raise ValueError("path must be an absolute drive path such as C:\\data")
    drive = match.group(1).lower()
    remainder = match.group(2).replace("\\", "/")
    if ":" in remainder:
        raise ValueError("colon is not allowed after the drive prefix")
    parts = remainder.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("parent traversal is not allowed")
    normalized = "/".join(part for part in parts if part not in {"", "."})
    return f"/mnt/{drive}" + (f"/{normalized}" if normalized else "")
