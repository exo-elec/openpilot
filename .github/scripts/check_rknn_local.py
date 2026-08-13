#!/usr/bin/env python3
"""Verify that RKNN model references in EOP10 are locally built/owned.

Forbidden in public EOP10:
  - External/vendor-branded source names (kommu, KA2, bukapilot, kmini, ...)
  - Automatic download URLs for .rknn models
  - References to forked/vendored RKNN model repositories
  - Inference-registry paths pointing outside the repo or /data/openpilot/models

Allowed:
  - Local ONNX -> RKNN conversion via tools/convert_models_to_rknn.py
  - Generic public model-zoo references for optional segmentation/detection
    ONNX sources in models/README.md and models/download_models.sh
  - Local /data/openpilot/models/rknn/... deployment paths
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = re.compile(
    r"kommu|KA2|bukapilot|kmini|kommuai|"
    r"https?://[^\s\"']+\.rknn|"
    r"\.rknn\s*url|download.*\.rknn.*url|"
    r"fork.*rknn|vendored.*rknn|external.*rknn\s*model",
    re.IGNORECASE,
)

REGISTRY = REPO_ROOT / "selfdrive/modeld/models/inference_registry.yaml"
CONVERTER = REPO_ROOT / "tools/convert_models_to_rknn.py"
DOWNLOADER = REPO_ROOT / "selfdrive/modeld/vision/models/download_models.py"


def check_file(path: Path, allowed: re.Pattern | None = None) -> list[str]:
    """Return list of forbidden findings in a file."""
    if not path.exists():
        return [f"{path}: file missing"]
    findings: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if allowed and allowed.search(line):
            continue
        if FORBIDDEN.search(line):
            findings.append(f"{path}:{lineno}: {line.strip()}")
    return findings


def check_registry_paths() -> list[str]:
    """Ensure inference_registry.yaml only references local model paths."""
    findings: list[str] = []
    text = REGISTRY.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("path:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            allowed_prefixes = (
                "/data/openpilot/models/",
                "selfdrive/modeld/models/",
                "models/",
            )
            if not any(value.startswith(p) for p in allowed_prefixes):
                findings.append(
                    f"{REGISTRY}:{lineno}: non-local model path: {value}"
                )
    return findings


def main() -> int:
    findings: list[str] = []

    # Allow generic public model-zoo URLs in the helper script/README.
    zoo_url = re.compile(
        r"https?://github\.com/(airockchip/rknn_model_zoo|hailo-ai/hailo_model_zoo)|"
        r"https?://hailo-model-zoo\.s3\.[^\s\"']+\.hef",
    )

    findings.extend(check_file(REGISTRY))
    findings.extend(check_file(CONVERTER))
    findings.extend(check_file(DOWNLOADER))
    findings.extend(check_registry_paths())

    # Optional sanity scan of modeld Python files (excluding generic zoo docs).
    for py_file in (REPO_ROOT / "selfdrive/modeld").rglob("*.py"):
        findings.extend(check_file(py_file))

    if findings:
        print("RKNN local-placement check FAILED:")
        for f in findings:
            print(f"  {f}")
        return 1

    print("RKNN local-placement check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
