#!/usr/bin/env python3
"""Build a deterministic v2 ONNX-only model ZIP and its measured release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iPhoto.pets.model_contract import PET_MODEL_MANIFEST, dinov2_metadata


def identity(payload: bytes) -> dict:
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def build(model_root: Path, output: Path, *, url: str | None = None) -> dict:
    if url and (urlsplit(url).scheme != "https" or not urlsplit(url).netloc):
        raise ValueError("Release URL must use HTTPS")
    detector, embedder = (PET_MODEL_MANIFEST[key] for key in ("detector", "embedder"))
    people = ["buffalo_s/det_500m.onnx", "buffalo_s/w600k_mbf.onnx"]
    pets = [f"pets/{detector['filename']}", f"pets/{embedder['filename']}"]
    metadata_path = f"{pets[1]}.metadata.json"
    data = {name: (model_root / name).read_bytes() for name in [*people, *pets]}
    if any(not payload for payload in data.values()):
        raise ValueError("Models must not be empty")
    previous_contract = json.loads(
        (ROOT / "src/iPhoto/recognition/recognition_package.json").read_text()
    )
    for name in people:
        if identity(data[name]) != previous_contract["files"][name]:
            raise ValueError(f"People model differs from the verified Buffalo S release: {name}")
    if identity(data[pets[0]])["sha256"] != detector["sha256"]:
        raise ValueError("YOLOX does not match the runtime manifest")
    if identity(data[pets[1]]) != {
        "size": embedder["onnx_size"],
        "sha256": embedder["onnx_sha256"],
    }:
        raise ValueError("DINOv2 does not match the runtime manifest")
    expected_metadata = dinov2_metadata(embedder, embedder["model_name"])
    if json.loads((model_root / metadata_path).read_text()) != expected_metadata:
        raise ValueError("DINOv2 metadata does not match the runtime manifest")
    data[metadata_path] = (json.dumps(expected_metadata, indent=2, sort_keys=True) + "\n").encode()
    pets.append(metadata_path)
    manifest = {
        "package": "iphotron-recognition-models",
        "schema_version": 1,
        "version": 2,
        "components": {"people": {"files": people}, "pets": {"files": pets}},
        "files": {name: identity(payload) for name, payload in sorted(data.items())},
    }
    data["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, payload in sorted(data.items()):
                entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(
                    entry, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
                )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "version": 2,
        "filename": output.name,
        **identity(output.read_bytes()),
        "url": url,
        "release_status": "published" if url else "pending_publication",
        "files": manifest["files"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--published-url", help="Set only after uploading and verifying this exact ZIP"
    )
    args = parser.parse_args()
    contract = build(args.model_root, args.output, url=args.published_url)
    if args.published_url:
        # Publication is a separate maintainer action. Confirm its attachment
        # bytes before embedding a URL in a production download contract.
        digest, total = hashlib.sha256(), 0
        with urlopen(args.published_url, timeout=60) as response:
            if not response.geturl().startswith("https://"):
                raise ValueError("Release attachment redirected away from HTTPS")
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > contract["size"]:
                    raise ValueError("Published attachment exceeds generated ZIP size")
                digest.update(chunk)
        if total != contract["size"] or digest.hexdigest() != contract["sha256"]:
            raise ValueError("Published attachment differs from the generated ZIP")
    args.contract.parent.mkdir(parents=True, exist_ok=True)
    from export_dinov2_onnx import atomic_json

    atomic_json(args.contract, contract)
    print(json.dumps(contract, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
