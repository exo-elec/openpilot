#!/usr/bin/env python3
"""
Download RKNN model files for EOP10 (EnhancedOpenPilot).

Models:
  - sceneseg_lite_rk3588.rknn: SceneSeg foreground segmentation (NPU Core 1)

Usage:
  python download_models.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# Model registry
MODELS = {
    "sceneseg_lite_rk3588.rknn": {
        # EOP: Models must be manually placed in this directory.
        # No automatic download — offline-first operation.
        "url": None,
        "sha256": None,  # Add SHA256 checksum when available
        "size_mb": 8,
        "description": "SceneSeg Lite foreground segmentation for RK3588 NPU",
    },
}

MODEL_DIR = Path(__file__).parent


def download_file(url: str, dest: Path, chunk_size: int = 8192) -> None:
    """Download file with progress."""
    print(f"Downloading: {url}")
    print(f"Destination: {dest}")

    req = Request(url, headers={"User-Agent": "EOP10-ModelDownloader/1.0"})

    try:
        with urlopen(req) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0

            with open(dest, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r  Progress: {percent:.1f}% ({downloaded}/{total_size} bytes)", end='', flush=True)

        print(f"\n  Download complete: {dest}")
    except URLError as e:
        print(f"\n  Error downloading: {e}")
        if dest.exists():
            dest.unlink()
        raise


def verify_checksum(filepath: Path, expected_sha256: str | None) -> bool:
    """Verify file SHA256 checksum."""
    if expected_sha256 is None:
        return True  # Skip if no checksum provided

    print("  Verifying checksum...")
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    actual = sha256.hexdigest()
    if actual != expected_sha256:
        print("  Checksum mismatch!")
        print(f"    Expected: {expected_sha256}")
        print(f"    Actual:   {actual}")
        return False

    print("  Checksum verified")
    return True


def download_model(name: str, info: dict, force: bool = False) -> bool:
    """Download a single model."""
    print(f"\n{'='*60}")
    print(f"Model: {name}")
    print(f"Description: {info['description']}")
    print(f"Size: ~{info['size_mb']} MB")
    print(f"{'='*60}")

    dest = MODEL_DIR / name

    if dest.exists() and not force:
        print(f"  File already exists: {dest}")
        if info['sha256'] and verify_checksum(dest, info['sha256']):
            print("  Skipping download (use --force to overwrite)")
            return True
        else:
            print("  Re-downloading...")

    try:
        download_file(info['url'], dest)
        if info['sha256']:
            if not verify_checksum(dest, info['sha256']):
                print("  Warning: Checksum verification failed")
                return False
        return True
    except Exception as e:
        print(f"  Failed to download: {e}")
        return False


def create_placeholder(name: str) -> None:
    """Create a placeholder file with instructions."""
    placeholder = MODEL_DIR / f"{name}.PLACEHOLDER"
    content = f"""# Model Placeholder: {name}

This file indicates that the model '{name}' needs to be downloaded.

To download the model:
  python download_models.py

Or manually download from:
  {MODELS.get(name, {}).get('url', 'URL not configured')}

Place the downloaded file in this directory:
  {MODEL_DIR}

Model Information:
  - Name: {name}
  - Description: {MODELS.get(name, {}).get('description', 'N/A')}
  - Expected size: ~{MODELS.get(name, {}).get('size_mb', '?')} MB
"""
    placeholder.write_text(content)
    print(f"  Created placeholder: {placeholder}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download EOP10 RKNN models")
    parser.add_argument("--force", action="store_true", help="Force re-download existing files")
    parser.add_argument("--model", type=str, help="Download specific model only")
    parser.add_argument("--list", action="store_true", help="List available models")
    args = parser.parse_args()

    if args.list:
        print("Available models:")
        for name, info in MODELS.items():
            print(f"  {name}")
            print(f"    Description: {info['description']}")
            print(f"    Size: ~{info['size_mb']} MB")
            dest = MODEL_DIR / name
            status = "✓ Downloaded" if dest.exists() else "✗ Missing"
            print(f"    Status: {status}")
        return 0

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    # Check if models are available for download
    if not MODELS:
        print("No models configured for download.")
        print("Models must be manually added to the model registry.")
        return 0

    models_to_download = {args.model: MODELS[args.model]} if args.model else MODELS

    success_count = 0
    fail_count = 0

    for name, info in models_to_download.items():
        if name not in MODELS:
            print(f"Unknown model: {name}")
            fail_count += 1
            continue

        if download_model(name, info, args.force):
            success_count += 1
        else:
            fail_count += 1
            create_placeholder(name)

    print(f"\n{'='*60}")
    print(f"Download Summary: {success_count} succeeded, {fail_count} failed")
    print(f"{'='*60}")

    # Check if SceneSeg model is present (critical for gridd)
    sceneseg_model = MODEL_DIR / "sceneseg_lite_rk3588.rknn"
    if not sceneseg_model.exists():
        print("\n⚠️  WARNING: SceneSeg model is missing!")
        print("   gridd will run without foreground segmentation.")
        print("   OccupancyGrid will only use depth data.")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
