#!/usr/bin/env python3
"""
يستبدل ملفا (asset) واحدا داخل GitHub Release موجود مسبقا:
  1. يتاكد ان الملف الجديد المحمل صالح (حجمه > 0، وامتداده .apk).
  2. اذا كان asset بنفس الاسم موجودا بالفعل داخل الـ release، يحذفه.
  3. يرفع الملف الجديد بنفس الاسم بالضبط.

يعتمد على GitHub CLI (gh) المثبت مسبقا على ubuntu-latest، ومتغير البيئة
GH_TOKEN الذي توفره خطوة الـ workflow تلقائيا (secrets.GITHUB_TOKEN).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[replace_release_asset] {msg}", flush=True)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--asset-name", required=True, help="e.g. 0.apk")
    ap.add_argument("--file", required=True, help="local path to the new file")
    args = ap.parse_args()

    file_path = Path(args.file)

    if not file_path.exists() or file_path.stat().st_size == 0:
        log(f"ERROR: downloaded file is missing or empty: {file_path}")
        return 1

    size_mb = file_path.stat().st_size / (1024 * 1024)
    log(f"new file ready: {file_path} ({size_mb:.2f} MB)")

    # يتحقق ان الملف فعلا APK حقيقي (ملفات apk هي zip؛ توقيعها PK\x03\x04)
    with open(file_path, "rb") as f:
        head = f.read(4)
    if head[:2] != b"PK":
        log(
            "WARNING: the downloaded file does not look like a valid APK/ZIP "
            f"(signature={head!r}). It will still be uploaded, but please check "
            "the source link."
        )

    # 1) هل يوجد asset بنفس الاسم على الـ release؟
    r = run(
        [
            "gh", "release", "view", args.tag,
            "--repo", args.repo,
            "--json", "assets",
        ]
    )
    assets = json.loads(r.stdout).get("assets", [])
    existing = [a["name"] for a in assets]
    log(f"current assets on release {args.tag}: {existing}")

    if args.asset_name in existing:
        log(f"deleting old asset: {args.asset_name}")
        run(
            [
                "gh", "release", "delete-asset", args.tag, args.asset_name,
                "--repo", args.repo, "--yes",
            ]
        )
    else:
        log(f"no existing asset named {args.asset_name} (will just upload)")

    # 2) رفع الملف الجديد بنفس الاسم
    log(f"uploading new asset: {args.asset_name}")
    run(
        [
            "gh", "release", "upload", args.tag,
            f"{file_path}#{args.asset_name}",
            "--repo", args.repo,
            "--clobber",
        ]
    )

    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
