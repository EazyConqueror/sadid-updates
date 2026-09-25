#!/usr/bin/env python3
"""
يحمّل ملف APK واحد من رابط قد يكون:
  - رابط تحميل مباشر عادي (مثال: رابط GitHub Release، رابط مباشر لخادم)
  - رابط صفحة (مثل MediaFire) يحتاج متصفحا حقيقيا لاستخراج رابط التحميل منها
  - رابط APKMirror الذي يمر بصفحة وسيطة (Continue) قبل رابط التحميل الفعلي

الاستراتيجية:
  1) نحاول أولا معرفة نوع الرابط (Content-Type) بطلب HEAD بسيط.
     اذا كان يرجع مباشرة ملفا (application/vnd.android.package-archive
     او application/octet-stream وامتداد ينتهي .apk) => تحميل مباشر بـ curl.
  2) اذا لم يكن كذلك (صفحة HTML)، نفتح المتصفح الحقيقي (Chromium عبر
     Playwright) وننتظر زر التحميل او نلتقط اول طلب شبكة لملف .apk
     الذي يظهر بعد الضغط/التنقل، ثم نحمله بنفس جلسة المتصفح (كوكيز حقيقية).

  هذا يحل مشكلة رفض الخوادم للتحميل عند فحصها ان الطالب "ليس متصفحا حقيقيا"
  (User-Agent، Referer، JavaScript challenge، صفحة انتظار...الخ).
"""

import argparse
import mimetypes
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

APK_CONTENT_TYPES = {
    "application/vnd.android.package-archive",
    "application/octet-stream",
    "application/x-apk",
    "binary/octet-stream",
}

REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# مواقع معروفة بأنها صفحات وسيطة (وليست روابط تحميل مباشرة) حتى لو كان
# الامتداد في الرابط يوحي بغير ذلك، فنجبر استخدام المتصفح الحقيقي دائما لها.
FORCE_BROWSER_HOSTS = (
    "mediafire.com",
    "apkmirror.com",
    "drive.google.com",
    "mega.nz",
    "1drv.ms",
    "dropbox.com",
)


def log(msg: str) -> None:
    print(f"[download_apk] {msg}", flush=True)


def looks_like_direct_file(url: str, timeout: int = 20) -> tuple[bool, str]:
    """
    يفحص الرابط بطلب HEAD (وGET جزئي عند الحاجة) ليقرر هل هو ملف مباشر.
    يرجع (True/False, السبب).
    """
    host = urlparse(url).netloc.lower()
    if any(h in host for h in FORCE_BROWSER_HOSTS):
        return False, f"host {host} known to require a real browser"

    headers = {"User-Agent": REAL_UA}
    try:
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        cdisp = r.headers.get("Content-Disposition", "")

        if r.status_code >= 400:
            # بعض الخوادم ترفض HEAD وتقبل GET فقط
            r = requests.get(
                url, headers=headers, allow_redirects=True, timeout=timeout, stream=True
            )
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            cdisp = r.headers.get("Content-Disposition", "")
            r.close()

        if ".apk" in cdisp.lower():
            return True, "Content-Disposition names an .apk file"

        if ctype in APK_CONTENT_TYPES and r.url.lower().endswith(".apk"):
            return True, f"Content-Type={ctype} and URL ends with .apk"

        if ctype in APK_CONTENT_TYPES:
            return True, f"Content-Type={ctype}"

        return False, f"Content-Type={ctype or 'unknown'} (looks like a web page)"
    except requests.RequestException as e:
        return False, f"HEAD/GET check failed: {e}"


def download_direct(url: str, out_path: Path, timeout: int = 1800) -> None:
    """تحميل مباشر بمكتبة requests (تكافئ curl -L) مع شريط تقدم بسيط."""
    headers = {"User-Agent": REAL_UA}
    with requests.get(
        url, headers=headers, stream=True, allow_redirects=True, timeout=timeout
    ) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {pct}% ({done}/{total} bytes)", end="", flush=True)
        print()


def download_with_real_browser(url: str, out_path: Path, timeout_ms: int = 120_000) -> None:
    """
    يفتح Chromium حقيقي (غير headless، عبر Xvfb) ليتصرف كمتصفح بشري كامل:
    JS، الكوكيز، التنقل بين الصفحات الوسيطة، وأزرار "Continue/Download".

    يعمل بطريقتين معا (ايهما نجحت اولا تفوز):
      أ) مراقبة الشبكة: يلتقط اول استجابة فيها ملف .apk فعلي اثناء التصفح.
      ب) البحث عن رابط/زر تحميل معروف والضغط عليه ثم انتظار حدث "التحميل".
    """
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    captured = {"path": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # متصفح حقيقي كامل (يعمل داخل Xvfb على الـ runner)
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=REAL_UA,
            viewport={"width": 1366, "height": 900},
            accept_downloads=True,
        )
        # يخفي بعض علامات "انا اوتوميشن" البدائية التي تتحقق منها بعض المواقع
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # (أ) التقاط اي استجابة شبكة فيها ملف apk اثناء التصفح
        def on_response(resp):
            try:
                ctype = resp.headers.get("content-type", "").lower()
                cdisp = resp.headers.get("content-disposition", "").lower()
                if resp.url.lower().split("?")[0].endswith(".apk") or ".apk" in cdisp or (
                    ctype in APK_CONTENT_TYPES and resp.request.resource_type in ("document", "other")
                ):
                    if captured["path"] is None:
                        log(f"network response looks like an APK: {resp.url[:120]}")
            except Exception:
                pass

        page.on("response", on_response)

        log(f"opening page: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)

        # ازرار تحميل شائعة (عربي/انجليزي) على مواقع مثل APKMirror/MediaFire
        candidate_selectors = [
            "a#downloadButton",  # mediafire
            "a.downloadButton",
            "a[href*='download']:has-text('Download')",
            "a:has-text('Continue')",
            "a:has-text('Download APK')",
            "button:has-text('Download')",
            "a:has-text('تحميل')",
            "a:has-text('تنزيل')",
        ]

        clicked_any = False
        for _ in range(3):  # بعض المواقع فيها اكثر من صفحة وسيطة متتالية
            clicked = False
            for sel in candidate_selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() and el.is_visible(timeout=1500):
                        log(f"clicking: {sel}")
                        with page.expect_download(timeout=15_000) as dl_info:
                            el.click(timeout=5000)
                        download = dl_info.value
                        download.save_as(str(out_path))
                        captured["path"] = out_path
                        clicked = True
                        clicked_any = True
                        break
                except Exception:
                    continue
            if captured["path"] or not clicked:
                break
            page.wait_for_timeout(2500)

        if captured["path"] is None:
            # لم يلتقط تحميلا عبر الازرار المعروفة: كملاذ اخير ننتظر اي حدث تحميل
            # قد يبدأ من تلقاء نفسه (بعض الصفحات تبدأ التحميل بعد عد تنازلي)
            log("no known download button matched; waiting for an automatic download...")
            try:
                with page.expect_download(timeout=30_000) as dl_info:
                    pass
                download = dl_info.value
                download.save_as(str(out_path))
                captured["path"] = out_path
            except Exception as e:
                browser.close()
                raise RuntimeError(
                    "Real browser could not find/trigger a download on this page. "
                    f"Last error: {e}"
                )

        browser.close()

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Download finished but the file is empty or missing.")

    log(f"saved via real browser: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_path = Path(args.out)
    url = args.url.strip()

    log(f"URL: {url}")
    is_direct, reason = looks_like_direct_file(url)
    log(f"direct-file check: {is_direct} ({reason})")

    if is_direct:
        try:
            download_direct(url, out_path)
            if out_path.exists() and out_path.stat().st_size > 0:
                log(f"saved via direct download: {out_path} ({out_path.stat().st_size} bytes)")
                return 0
            log("direct download produced an empty file, falling back to real browser")
        except Exception as e:
            log(f"direct download failed ({e}), falling back to real browser")

    download_with_real_browser(url, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
