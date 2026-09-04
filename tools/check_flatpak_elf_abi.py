#!/usr/bin/env python3
"""Validate Flatpak standalone provenance, icon, and ELF ABI requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

_ELF_MAGIC = b"\x7fELF"
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_EM_X86_64 = 62
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_VERSION_PATTERN = re.compile(r"\bName:\s*(GLIBCXX|GLIBC|CXXABI)_([0-9]+(?:\.[0-9]+)+)\b")
_DEFAULT_LIMITS = {
    "GLIBC": "2.42",
    "GLIBCXX": "3.4.34",
    "CXXABI": "1.3.15",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_or_unavailable(path: Path) -> str:
    try:
        return _sha256_file(path)
    except OSError:
        return "unavailable"


def _sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    root_mode = stat.S_IMODE(path.lstat().st_mode)
    digest.update(f"D\0{root_mode:o}\0.\0".encode("utf-8"))
    for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()):
        item_stat = item.lstat()
        relative = item.relative_to(path).as_posix()
        mode = stat.S_IMODE(item_stat.st_mode)
        if stat.S_ISLNK(item_stat.st_mode):
            kind = "L"
            payload = os.readlink(item).encode("utf-8", errors="surrogateescape")
        elif stat.S_ISREG(item_stat.st_mode):
            kind = "F"
            payload = _sha256_file(item).encode("ascii")
        elif stat.S_ISDIR(item_stat.st_mode):
            kind = "D"
            payload = b""
        else:
            kind = "O"
            payload = b""
        digest.update(kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(f"{mode:o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_tree_or_unavailable(path: Path) -> str:
    try:
        return _sha256_tree(path)
    except OSError:
        return "unavailable"


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def parse_version_needs(output: str) -> dict[str, set[str]]:
    """Return numeric GLIBC-family names from readelf's version-needs sections."""

    requirements = {family: set() for family in _DEFAULT_LIMITS}
    in_needs = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Version needs section"):
            in_needs = True
            continue
        if stripped.startswith("Version ") and not stripped.startswith(
            "Version needs section"
        ):
            in_needs = False
        if not in_needs:
            continue
        for family, version in _VERSION_PATTERN.findall(line):
            requirements[family].add(version)
    return requirements


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _elf_identity(path: Path) -> tuple[dict[str, int], list[str]]:
    relative_label = path.name
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
    except OSError as exc:
        return {}, [f"cannot read ELF header for {relative_label}: {exc}"]
    if len(header) < 20 or not header.startswith(_ELF_MAGIC):
        return {}, [f"ELF header is truncated for {relative_label}"]

    elf_class = int(header[4])
    data_encoding = int(header[5])
    byte_order = "little" if data_encoding == _ELFDATA2LSB else "big"
    machine = int.from_bytes(header[18:20], byteorder=byte_order)
    errors: list[str] = []
    if elf_class != _ELFCLASS64:
        errors.append(f"{relative_label} is not ELFCLASS64")
    if data_encoding != _ELFDATA2LSB:
        errors.append(f"{relative_label} is not little-endian ELF")
    if machine != _EM_X86_64:
        errors.append(f"{relative_label} has e_machine={machine}, expected EM_X86_64=62")
    return {
        "elf_class": elf_class,
        "data_encoding": data_encoding,
        "machine": machine,
    }, errors


def _discover_elf_files(root: Path) -> tuple[list[Path], list[str]]:
    elf_files: list[Path] = []
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() and not path.exists():
            errors.append(f"payload contains a broken symlink: {path.relative_to(root)}")
            continue
        try:
            resolved = path.resolve()
        except OSError as exc:
            errors.append(f"cannot resolve payload path {path}: {exc}")
            continue
        if not resolved.is_relative_to(root):
            errors.append(f"payload path escapes the standalone root: {path.relative_to(root)}")
            continue
        try:
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                magic = stream.read(4)
        except OSError as exc:
            errors.append(f"cannot inspect payload file {path.relative_to(root)}: {exc}")
            continue
        if magic == _ELF_MAGIC:
            elf_files.append(path)
    return elf_files, errors


def _validate_png(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return [f"cannot read Flatpak icon {path}: {exc}"]
    if not payload.startswith(_PNG_SIGNATURE):
        return ["Flatpak icon is not a PNG file"]

    offset = len(_PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(payload):
            errors.append("Flatpak PNG contains a truncated chunk")
            break
        chunk_type = payload[offset + 4 : offset + 8]
        data = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            errors.append(f"Flatpak PNG has an invalid {chunk_type!r} CRC")
        chunks.append((chunk_type, data))
        offset = end
        if chunk_type == b"IEND":
            break

    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        errors.append("Flatpak PNG is missing a valid IHDR chunk")
        return errors
    width, height = struct.unpack(">II", chunks[0][1][:8])
    if (width, height) != (256, 256):
        errors.append(f"Flatpak icon must be 256x256 pixels, got {width}x{height}")
    if not any(chunk_type == b"IDAT" for chunk_type, _data in chunks):
        errors.append("Flatpak PNG is missing image data")
    if not chunks or chunks[-1][0] != b"IEND":
        errors.append("Flatpak PNG is missing its IEND chunk")
    return errors


def _validate_provenance(
    *,
    root: Path,
    entrypoint: Path,
    manifest_path: Path,
    expected_source_revision: str,
    expected_project_version: str,
    expected_build_driver: Path,
    required_distro_id: str,
    required_distro_version: str,
    required_machine: str,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read standalone build manifest: {exc}"]
    environment = manifest.get("environment")
    build_host = environment.get("build_host") if isinstance(environment, dict) else None
    if not isinstance(build_host, dict):
        return manifest, ["standalone build manifest has no build_host provenance"]

    expected = {
        "system": "Linux",
        "machine": required_machine,
        "distro_id": required_distro_id,
        "distro_version_id": required_distro_version,
        "libc_name": "glibc",
        "libc_version": "2.39",
    }
    for key, expected_value in expected.items():
        actual = str(build_host.get(key) or "")
        if actual.casefold() != expected_value.casefold():
            errors.append(
                f"standalone build host {key} must be {expected_value!r}, got {actual!r}"
            )

    actual_revision = str(manifest.get("source_revision") or "")
    if actual_revision != expected_source_revision:
        errors.append(
            "standalone source_revision does not match the current checkout: "
            f"expected {expected_source_revision!r}, got {actual_revision!r}"
        )

    expected_driver_sha256 = _sha256_or_unavailable(expected_build_driver)
    actual_driver_sha256 = str(environment.get("build_driver_sha256") or "").lower()
    if expected_driver_sha256 == "unavailable":
        errors.append(f"current Nuitka build driver cannot be hashed: {expected_build_driver}")
    elif actual_driver_sha256 != expected_driver_sha256:
        errors.append(
            "standalone build_driver_sha256 does not match the current "
            "scripts/build_nuitka_fast.sh"
        )

    build_flags = environment.get("build_flags")
    expected_version_flag = f"project_version={expected_project_version}"
    project_version_flags = (
        [str(flag) for flag in build_flags if str(flag).startswith("project_version=")]
        if isinstance(build_flags, list)
        else []
    )
    if project_version_flags != [expected_version_flag]:
        errors.append(
            "standalone project_version does not match the current checkout: "
            f"expected {expected_version_flag!r}, got {project_version_flags!r}"
        )

    if str(manifest.get("artifact_path") or "") != entrypoint.name:
        errors.append("standalone build manifest artifact_path does not match the entrypoint")
    actual_sha256 = _sha256_or_unavailable(entrypoint)
    if actual_sha256 == "unavailable":
        errors.append("standalone entrypoint could not be hashed")
    if str(manifest.get("artifact_sha256") or "").lower() != actual_sha256:
        errors.append("standalone build manifest artifact_sha256 does not match the entrypoint")
    if str(manifest.get("artifact_tree_path") or "") != root.name:
        errors.append("standalone build manifest artifact_tree_path does not match the payload")
    try:
        actual_tree_sha256 = _sha256_tree(root)
    except OSError as exc:
        errors.append(f"standalone payload could not be hashed: {exc}")
    else:
        if str(manifest.get("artifact_tree_sha256") or "").lower() != actual_tree_sha256:
            errors.append(
                "standalone build manifest artifact_tree_sha256 does not match the payload"
            )
    return manifest, errors


def audit_payload(
    *,
    root: Path,
    entrypoint: Path,
    build_manifest: Path,
    icon: Path,
    expected_source_revision: str,
    expected_project_version: str,
    expected_build_driver: Path,
    limits: dict[str, str],
    readelf_bin: str,
    required_distro_id: str = "ubuntu",
    required_distro_version: str = "24.04",
    required_machine: str = "x86_64",
) -> dict[str, Any]:
    root = root.resolve()
    entrypoint = entrypoint.resolve()
    errors = _validate_png(icon.resolve())
    manifest, provenance_errors = _validate_provenance(
        root=root,
        entrypoint=entrypoint,
        manifest_path=build_manifest.resolve(),
        expected_source_revision=expected_source_revision,
        expected_project_version=expected_project_version,
        expected_build_driver=expected_build_driver.resolve(),
        required_distro_id=required_distro_id,
        required_distro_version=required_distro_version,
        required_machine=required_machine,
    )
    errors.extend(provenance_errors)
    if not entrypoint.is_relative_to(root) or not _is_elf(entrypoint):
        errors.append("Flatpak standalone entrypoint must be an ELF file inside the payload")

    elf_files, discovery_errors = _discover_elf_files(root)
    errors.extend(discovery_errors)
    if not elf_files:
        errors.append("Flatpak standalone contains no ELF files")

    highest: dict[str, str | None] = dict.fromkeys(limits)
    file_reports: list[dict[str, Any]] = []
    for path in elf_files:
        relative_path = path.relative_to(root)
        identity, identity_errors = _elf_identity(path)
        errors.extend(f"{relative_path}: {error}" for error in identity_errors)
        try:
            result = subprocess.run(  # noqa: S603 - explicit release-tool boundary
                [readelf_bin, "--version-info", "--wide", str(path)],
                check=False,
                capture_output=True,
                env={**os.environ, "LC_ALL": "C"},
                text=True,
            )
        except OSError as exc:
            errors.append(f"readelf failed for {relative_path}: {exc}")
            continue
        if result.returncode != 0:
            reason = result.stderr.strip() or f"exit {result.returncode}"
            errors.append(f"readelf failed for {relative_path}: {reason}")
            continue
        requirements = parse_version_needs(result.stdout)
        normalized = {
            family: sorted(versions, key=_version_key)
            for family, versions in requirements.items()
            if versions
        }
        file_reports.append(
            {
                "path": relative_path.as_posix(),
                "elf": identity,
                "requirements": normalized,
            }
        )
        if path.resolve() == entrypoint and not normalized.get("GLIBC"):
            errors.append("Flatpak standalone entrypoint has no readable GLIBC version needs")
        for family, versions in normalized.items():
            candidate = versions[-1]
            if highest[family] is None or _version_key(candidate) > _version_key(
                str(highest[family])
            ):
                highest[family] = candidate
            if _version_key(candidate) > _version_key(limits[family]):
                errors.append(
                    f"{relative_path} requires {family}_{candidate}, "
                    f"above {family}_{limits[family]}"
                )

    manifest_environment = manifest.get("environment") if isinstance(manifest, dict) else {}
    manifest_build_host = (
        manifest_environment.get("build_host", {})
        if isinstance(manifest_environment, dict)
        else {}
    )
    manifest_build_flags = (
        manifest_environment.get("build_flags", [])
        if isinstance(manifest_environment, dict)
        else []
    )
    return {
        "schema_version": 1,
        "passed": not errors,
        "root": root.name,
        "entrypoint": entrypoint.relative_to(root).as_posix()
        if entrypoint.is_relative_to(root)
        else str(entrypoint),
        "build_manifest": build_manifest.name,
        "build_manifest_sha256": _sha256_or_unavailable(build_manifest.resolve()),
        "entrypoint_sha256": _sha256_or_unavailable(entrypoint),
        "artifact_tree_sha256": _sha256_tree_or_unavailable(root),
        "build_host": manifest_build_host,
        "source_revision": manifest.get("source_revision")
        if isinstance(manifest, dict)
        else None,
        "build_driver_sha256": manifest_environment.get("build_driver_sha256")
        if isinstance(manifest_environment, dict)
        else None,
        "build_flags": manifest_build_flags,
        "expected_source_revision": expected_source_revision,
        "expected_project_version": expected_project_version,
        "expected_build_driver_sha256": _sha256_or_unavailable(
            expected_build_driver.resolve()
        ),
        "limits": limits,
        "highest_requirements": highest,
        "elf_file_count": len(elf_files),
        "files": file_reports,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--entrypoint", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--icon", type=Path, required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--expected-project-version", required=True)
    parser.add_argument("--expected-build-driver", type=Path, required=True)
    parser.add_argument("--max-glibc", default=_DEFAULT_LIMITS["GLIBC"])
    parser.add_argument("--max-glibcxx", default=_DEFAULT_LIMITS["GLIBCXX"])
    parser.add_argument("--max-cxxabi", default=_DEFAULT_LIMITS["CXXABI"])
    parser.add_argument("--readelf-bin", default="readelf")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_payload(
        root=args.root,
        entrypoint=args.entrypoint,
        build_manifest=args.build_manifest,
        icon=args.icon,
        expected_source_revision=args.expected_source_revision,
        expected_project_version=args.expected_project_version,
        expected_build_driver=args.expected_build_driver,
        limits={
            "GLIBC": args.max_glibc,
            "GLIBCXX": args.max_glibcxx,
            "CXXABI": args.max_cxxabi,
        },
        readelf_bin=args.readelf_bin,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report["passed"]:
        print(
            "Flatpak ELF ABI check passed: "
            f"{report['elf_file_count']} files, highest={report['highest_requirements']}"
        )
        return 0
    for error in report["errors"]:
        print(f"Flatpak input check failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
