#!/usr/bin/env python3
"""Keep the maintained EN/ZH/DE README release/runtime contracts aligned."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READMES = (
    ROOT / "README.md",
    ROOT / "docs/readme/README_zh-CN.md",
    ROOT / "docs/readme/README_de.md",
)

REQUIRED = (
    "v6.6.8",
    "v6.68-x86-setup.exe",
    "iphotron_6.6.8_amd64.deb",
    "iPhotron-6.6.8-x86_64.AppImage",
    "com.github.OliverZhaohaibin.iPhotron-6.6.8-x86_64.flatpak",
    "iPhoto.entrypoint:main",
    "DesktopCoordinatorRuntime",
    "species-bounded-single-link-v3",
    "torchscript_url",
    "BUILD_FLATPAK.md",
)


def main() -> int:
    failures: list[str] = []
    for path in READMES:
        if not path.is_file():
            failures.append(f"missing maintained README: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in REQUIRED:
            if token not in text:
                failures.append(f"{path.relative_to(ROOT)}: missing parity token {token!r}")

    if failures:
        print("README parity check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("README parity OK: EN/ZH/DE release and runtime contracts are aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
