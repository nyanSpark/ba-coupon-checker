#!/usr/bin/env python3
"""
Sequential SOCKS5 proxy tester using Playwright.
"""

from __future__ import annotations

import random
import time
from typing import List, Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =========================
# Config you can control
# =========================
TARGET_URL = ""
OUTPUT_FILE = "good_socks5_proxies.txt"

NAV_TIMEOUT_MS = 25_000

# Retry behavior:
# Number of retries AFTER the first attempt (0 = no retry)
RETRIES_PER_PROXY = 0
RETRY_WAIT_SECONDS = 10

# Delay between different proxy checks:
BETWEEN_CHECKS_MIN_SECONDS = 5
BETWEEN_CHECKS_MAX_SECONDS = 10

# =========================
# Paste your SOCKS5 proxy list here
# List provided by https://github.com/proxifly/free-proxy-list
# =========================
RAW_PROXIES_TEXT = r"""
socks5://193.233.254.8:1080
socks5://24.249.199.12:4145
socks5://192.111.139.163:19404
socks5://174.77.111.198:49547
socks5://98.178.72.21:10919
socks5://184.178.172.28:15294
socks5://184.178.172.25:15291
socks5://184.178.172.18:15280
socks5://72.214.108.67:4145
socks5://192.252.211.193:4145
socks5://208.65.90.3:4145
socks5://103.163.244.106:1080
socks5://198.8.84.3:4145
socks5://64.227.131.240:1080
socks5://8.210.89.96:1080
socks5://198.177.252.24:4145
socks5://198.177.254.131:4145
socks5://72.223.188.67:4145
socks5://72.207.113.97:4145
socks5://121.169.46.116:1090
socks5://47.243.94.125:1080
socks5://202.72.232.121:1080
socks5://47.86.41.142:1024
socks5://174.138.61.184:1080
socks5://193.233.254.8:1080
socks5://193.233.254.9:1080
socks5://193.233.254.7:1080
socks5://124.248.177.43:1080
socks5://124.248.189.223:1080
socks5://194.163.167.32:1080
socks5://89.148.196.156:1080
socks5://31.43.194.184:1080
socks5://194.163.182.6:1080
socks5://5.255.117.127:1080
socks5://5.255.113.177:1080
socks5://5.255.117.250:1080
socks5://77.41.167.137:1080
socks5://46.146.220.247:1080
socks5://88.216.68.41:9101
socks5://195.35.113.29:1080
socks5://46.8.69.113:1080
socks5://185.194.217.97:1080
socks5://5.199.166.251:9061
socks5://36.255.98.178:30296
socks5://36.255.98.160:4226
socks5://62.60.131.203:4359
socks5://36.255.98.151:13126
socks5://5.199.166.243:9114
socks5://46.146.204.175:1080
socks5://36.255.98.162:10809
socks5://36.255.98.161:5703
socks5://195.98.82.62:1080
socks5://193.221.203.121:1080
socks5://62.60.131.205:4145
""".strip()


def parse_proxies(raw: str) -> List[str]:
    proxies: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        proxies.append(line)
    return proxies


def sleep_between_checks() -> None:
    delay = random.uniform(BETWEEN_CHECKS_MIN_SECONDS, BETWEEN_CHECKS_MAX_SECONDS)
    print(f"  ⏳ Waiting {delay:.1f}s before next proxy...")
    time.sleep(delay)


def try_proxy_once(p, proxy_url: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Returns: (success, page_title, error_name)
    - success=True means page loaded and title is non-empty
    """
    browser = None
    try:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": proxy_url},
        )

        context = browser.new_context()
        page = context.new_page()

        page.goto(TARGET_URL, timeout=NAV_TIMEOUT_MS, wait_until="load")
        title = (page.title() or "").strip()

        if title:
            return True, title, None
        return False, None, "EMPTY_TITLE"

    except PlaywrightTimeoutError:
        return False, None, "TIMEOUT"
    except Exception as e:
        return False, None, type(e).__name__
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def main() -> None:
    proxies = parse_proxies(RAW_PROXIES_TEXT)
    good_lines: List[str] = []

    total_attempts_per_proxy = 1 + max(RETRIES_PER_PROXY, 0)
    print(
        f"Starting test: {len(proxies)} proxies | "
        f"{total_attempts_per_proxy} max attempts each | "
        f"between-check delay {BETWEEN_CHECKS_MIN_SECONDS}-{BETWEEN_CHECKS_MAX_SECONDS}s"
    )

    with sync_playwright() as p:
        for idx, proxy_url in enumerate(proxies, start=1):
            print(f"\n[{idx}/{len(proxies)}] Testing {proxy_url}")

            success = False
            last_err: Optional[str] = None
            last_title: Optional[str] = None

            for attempt in range(1, total_attempts_per_proxy + 1):
                ok, title, err = try_proxy_once(p, proxy_url)
                if ok:
                    success = True
                    last_title = title
                    break

                last_err = err
                print(f"  ❌ FAIL (attempt {attempt}/{total_attempts_per_proxy}) → {err}")

                # If we have remaining attempts, wait before retrying
                if attempt < total_attempts_per_proxy:
                    print(f"  🔁 Waiting {RETRY_WAIT_SECONDS}s before retry...")
                    time.sleep(RETRY_WAIT_SECONDS)

            if success:
                ip_port = proxy_url.replace("socks5://", "")
                good_lines.append(f"\"{ip_port}\",")
                print(f"  ✅ SUCCESS → {last_title}")
            else:
                print(f"  ❌ FINAL FAIL → {last_err}")

            sleep_between_checks()

    # Write results
    if good_lines:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(good_lines) + "\n")
        print(f"\n✅ Wrote {len(good_lines)} working proxies to {OUTPUT_FILE}")
    else:
        print("\n❌ No working proxies found; nothing written.")


if __name__ == "__main__":
    main()
