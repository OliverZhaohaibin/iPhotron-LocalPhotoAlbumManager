#!/usr/bin/env python3
"""Fetch only the verified legacy release input for isolated ONNX export CI."""

from __future__ import annotations

import argparse
import hashlib
import ssl
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

URL = "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/pet-models-v1/iphotron-recognition-models-v1.zip"
SIZE = 100497212
SHA256 = "73819048dcf2d9352758a544a751be67512c18e0be458c295551b7142e91fa28"
MODEL = "pets/embedding/dinov2_vits14/dinov2_vits14.pt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
        archive = Path(directory) / "v1.zip"
        digest = hashlib.sha256()
        total = 0
        with (
            urlopen(URL, timeout=60, context=ssl.create_default_context()) as response,
            archive.open("wb") as stream,
        ):
            if not response.geturl().startswith("https://"):
                raise RuntimeError("HTTPS is required")
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > SIZE:
                    raise RuntimeError("Legacy source ZIP exceeds its fixed size")
                digest.update(chunk)
                stream.write(chunk)
        if total != SIZE or digest.hexdigest() != SHA256:
            raise RuntimeError("Legacy source ZIP integrity check failed")
        with zipfile.ZipFile(archive) as zipped:
            # Never extract untrusted paths or the old application layout.
            payload = zipped.read(MODEL)
        from export_dinov2_onnx import SOURCE_SHA256, SOURCE_SIZE

        if len(payload) != SOURCE_SIZE or hashlib.sha256(payload).hexdigest() != SOURCE_SHA256:
            raise RuntimeError("Legacy DINOv2 integrity check failed")
        staged = Path(directory) / "dinov2_vits14.pt"
        staged.write_bytes(payload)
        staged.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
