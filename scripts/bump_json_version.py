#!/usr/bin/env python3
"""
يحدّث ملف JSON المرتبط بملف APK بعد نجاح رفعه:
  1. يقرأ الملف الحالي من فرع main (مثال: WhatsAppUpdata/WhatsApp.json)
  2. يزيد آخر رقم في "version" بمقدار 1 (مثال: 1.2.4.1 -> 1.2.4.2)
  3. يحدّث "url" ليطابق رابط الـ Release الحالي (بنفس الاسم والـ tag)
  4. يكتب الملف ويعمل commit + push الى فرع main (وليس files)

بنية "version" المتوقعة: اربعة اجزاء مفصولة بنقطة، مثل 1.2.4.1
اذا كانت البنية مختلفة (اقل او اكثر من 4 اجزاء)، يوقف العملية برسالة
واضحة بدل تخمين شيء خاطئ.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[bump_json_version] {msg}", flush=True)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def bump_last_segment(version: str) -> str:
    """
    عداد ترقيمي بقاعدة 10 على آخر جزء، بترحيل (carry) الى ما قبله:
        1.2.4.1 -> 1.2.4.2
        1.2.4.9 -> 1.2.5.0   (وليس 1.2.4.10)
        1.2.9.9 -> 1.3.0.0
        1.9.9.9 -> 2.0.0.0

    يرفض اي بنية غير 4 اجزاء رقمية، بدل تخمين شيء قد يكسر تطبيقات
    تتحقق من رقم الاصدار.
    """
    parts = version.strip().split(".")
    if len(parts) != 4:
        raise ValueError(
            f"expected a 4-part version like 1.2.4.1, got '{version}' "
            f"({len(parts)} parts)"
        )
    if not all(p.isdigit() for p in parts):
        raise ValueError(
            f"all 4 version segments must be plain integers, got '{version}'"
        )

    digits = [int(p) for p in parts]

    # نزيد آخر خانة، وإن وصلت 10 نصفّرها وننقل +1 للخانة السابقة (ترحيل)،
    # وهكذا حتى اول خانة (والتي لا تُصفَّر أبدا، فقط تزداد بلا حد أعلى)
    i = len(digits) - 1
    digits[i] += 1
    while digits[i] >= 10 and i > 0:
        digits[i] = 0
        i -= 1
        digits[i] += 1

    return ".".join(str(d) for d in digits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-path", required=True, help="e.g. WhatsAppUpdata/WhatsApp.json")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--asset-name", required=True, help="e.g. 0.apk")
    args = ap.parse_args()

    json_path = Path(args.json_path)

    if not json_path.exists():
        log(f"ERROR: {json_path} does not exist in this checkout")
        return 1

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"ERROR: {json_path} is not valid JSON: {e}")
        return 1

    if "version" not in data:
        log(f"ERROR: {json_path} has no \"version\" key")
        return 1

    old_version = str(data["version"])
    try:
        new_version = bump_last_segment(old_version)
    except ValueError as e:
        log(f"ERROR: {e}")
        return 1

    new_url = (
        f"https://github.com/{args.repo}/releases/download/{args.tag}/{args.asset_name}"
    )

    data["version"] = new_version
    data["url"] = new_url

    json_path.write_text(
        json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    log(f"{json_path}: version {old_version} -> {new_version}")
    log(f"{json_path}: url -> {new_url}")

    # commit + push الى فرع main (الملف موجود اصلا هناك، بعكس فرع files)
    run(["git", "add", str(json_path)])

    diff = run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        log("no actual change to commit (version/url already matched)")
        return 0

    run(
        [
            "git", "commit", "-m",
            f"bump {json_path.name} to {new_version} ({args.asset_name})",
        ]
    )

    push_result = run(["git", "push", "origin", "main"], check=False)
    if push_result.returncode != 0:
        log(f"ERROR: git push failed: {push_result.stderr.strip()}")
        return 1

    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
