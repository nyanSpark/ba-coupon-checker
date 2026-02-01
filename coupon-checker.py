#!/usr/bin/env python3
"""
Automated Web Form Tester (single-layer: scheduler + runner)
"""

from __future__ import annotations

import random
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


@dataclass(frozen=True)
class Config:
    # User Defined Inputs
    URL: str = ""
    DISCORD_WEBHOOK_URL: str = ""
    MEMBER_CODE_VALUE: str = ""

    # Discord behavior
    ALWAYS_SEND_DISCORD: bool = False
    SEND_SCREENSHOT_ON_EVERY_RUN: bool = True

    # Browser behavior
    HEADLESS: bool = True
    NAVIGATION_TIMEOUT_MS: int = 10_000
    ACTION_TIMEOUT_MS: int = 10_000

    # Schedule
    RUN_ONCE: bool = False
    INTERVAL_SECONDS: int = 5
    RANDOM_INTERVAL_MIN: int = 1
    RANDOM_INTERVAL_MAX: int = 55

    # Stop on unexpected popup message
    STOP_ON_UNEXPECTED: bool = True

    # Basic page check
    HEALTHCHECK_SELECTOR: str = "body"

    # Region dropdown selection
    REGION_SELECT_SELECTOR: str = "#eRedeemRegion"
    REGION_VALUE: str = "na"

    # Inputs
    MEMBER_CODE_SELECTOR: str = "#eRedeemNpaCode"
    COUPON_CODE_SELECTOR: str = "#eRedeemCoupon"

    # Redeem button
    REDEEM_BUTTON_SELECTOR: str = "button.btn_confirm.e-characters-with-npacode[data-message='redeem']"

    # Popup specifics
    POPUP_ROOT_SELECTOR: str = "#popAlert"
    POPUP_ON_SELECTOR: str = "#popAlert.pop.on"
    POPUP_MESSAGE_SELECTOR: str = "#popAlert p.pop_msg"

    EXPECTED_POPUP_MESSAGE_HTML: str = (
        "The coupon cannot be used in this game.<br>Please check the coupon number again."
    )

    POPUP_ON_WAIT_TIMEOUT_MS: int = 10_000

    # Screenshot
    SCREENSHOT_DIR: str = "screenshots"

    # -------------------------
    # SOCKS5 Proxy support
    # -------------------------
    SOCKS5_PROXIES: Tuple[str, ...] = (
        "174.138.61.184:1080",
        "193.233.254.8:1080",
        "121.169.46.116:1090",
        "185.194.217.97:1080",
        "194.163.167.32:1080",
        "195.35.113.29:1080",
        "64.227.131.240:1080",
        "174.138.61.184:1080",
        "195.35.113.29:1080",
    )
    SOCKS5_USERNAME: str = ""
    SOCKS5_PASSWORD: str = ""

    # -------------------------
    # Proxy IP verification (toggleable)
    # -------------------------
    ENABLE_PROXY_IP_CHECK: bool = False
    PROXY_IP_CHECK_URL: str = "https://api.ipify.org?format=json"
    PROXY_IP_CHECK_TIMEOUT_MS: int = 8_000

    # -------------------------
    # Logging verbosity
    # -------------------------
    # If False: even non-proxy failures print one-line only (no traceback dumps).
    # If True: non-proxy failures will include a traceback in stdout (old behavior).
    VERBOSE_NON_PROXY_ERRORS: bool = False


CFG = Config()

ALPHANUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def send_discord(webhook_url: str, content: str, file_path: Optional[str] = None) -> None:
    if not webhook_url:
        return

    if file_path is None:
        resp = requests.post(webhook_url, json={"content": content}, timeout=15)
        resp.raise_for_status()
        return

    with open(file_path, "rb") as f:
        files = {"file": (Path(file_path).name, f, "image/png")}
        data = {"content": content}
        resp = requests.post(webhook_url, data=data, files=files, timeout=30)
        resp.raise_for_status()


def _normalize_socks5_proxy(server: str) -> str:
    s = server.strip()
    if not s:
        return s
    if "://" not in s:
        s = f"socks5://{s}"
    return s


def _pick_playwright_socks5_proxy(cfg: Config) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    if not cfg.SOCKS5_PROXIES:
        return None, None

    choice = random.choice(cfg.SOCKS5_PROXIES)
    server = _normalize_socks5_proxy(choice)
    if not server:
        return None, None

    proxy: Dict[str, str] = {"server": server}
    if cfg.SOCKS5_USERNAME:
        proxy["username"] = cfg.SOCKS5_USERNAME
    if cfg.SOCKS5_PASSWORD:
        proxy["password"] = cfg.SOCKS5_PASSWORD

    return proxy, server


def generate_coupon_code(rng: random.Random) -> str:
    counts: Dict[str, int] = {}

    def can_use(ch: str, prev: Optional[str]) -> bool:
        if prev is not None and ch == prev:
            return False
        if counts.get(ch, 0) >= 2:
            return False
        return True

    def pick_from(charset: str, prev: Optional[str]) -> str:
        for _ in range(300):
            ch = rng.choice(charset)
            if can_use(ch, prev):
                return ch
        raise RuntimeError("Generator got stuck due to constraints.")

    for _attempt in range(300):
        counts.clear()
        out = []
        prev: Optional[str] = None
        try:
            for _ in range(5):
                ch = pick_from(ALPHANUM, prev)
                out.append(ch)
                counts[ch] = counts.get(ch, 0) + 1
                prev = ch
            for _ in range(5):
                ch = pick_from(LETTERS, prev)
                out.append(ch)
                counts[ch] = counts.get(ch, 0) + 1
                prev = ch
            return "".join(out)
        except RuntimeError:
            continue

    raise RuntimeError("Unable to generate a coupon code after many attempts.")


def save_screenshot(page, screenshot_dir: str, prefix: str) -> str:
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    path = str(Path(screenshot_dir) / f"{prefix}_{ts}.png")
    page.screenshot(path=path, full_page=True)
    return path


def _is_proxyish_playwright_error(err: Exception) -> bool:
    msg = str(err)
    markers = [
        "net::ERR_PROXY",
        "net::ERR_NO_SUPPORTED_PROXIES",
        "net::ERR_TUNNEL_CONNECTION_FAILED",
        "net::ERR_CONNECTION_RESET",
        "net::ERR_CONNECTION_CLOSED",
        "net::ERR_CONNECTION_REFUSED",
        "net::ERR_ADDRESS_UNREACHABLE",
        "net::ERR_NAME_NOT_RESOLVED",
        "net::ERR_TIMED_OUT",
        "net::ERR_EMPTY_RESPONSE",
        "Proxy",
    ]
    return any(m in msg for m in markers)


def _one_line_error(e: Exception, limit: int = 180) -> str:
    s = str(e).replace("\n", " ").strip()
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return s or type(e).__name__


def _check_public_ip_via_proxy(context, cfg: Config) -> Optional[str]:
    try:
        resp = context.request.get(cfg.PROXY_IP_CHECK_URL, timeout=cfg.PROXY_IP_CHECK_TIMEOUT_MS)
        if not resp.ok:
            return None
        data = resp.json()
        ip = (data.get("ip") if isinstance(data, dict) else None) or None
        return str(ip).strip() if ip else None
    except Exception:
        return None


def run_once(cfg: Config) -> Tuple[bool, str, bool, bool]:
    """
    Returns:
      (ok, message, should_stop, proxy_load_error)
    """
    rng = random.Random()
    coupon_code_used: Optional[str] = None
    proxy_load_error = False
    observed_proxy_ip: Optional[str] = None
    proxy_server: Optional[str] = None

    with sync_playwright() as p:
        proxy, proxy_server = _pick_playwright_socks5_proxy(cfg)
        print(f"Proxy Picked: {proxy}")

        try:
            browser = (
                p.chromium.launch(headless=cfg.HEADLESS, proxy=proxy)
                if proxy
                else p.chromium.launch(headless=cfg.HEADLESS)
            )
        except Exception as e:
            if proxy_server:
                proxy_load_error = True
                print(f"⚠️ PROXY FAIL {proxy_server} → {_one_line_error(e)}")
            raise

        context = browser.new_context()
        page = context.new_page()
        page.set_default_navigation_timeout(cfg.NAVIGATION_TIMEOUT_MS)
        page.set_default_timeout(cfg.ACTION_TIMEOUT_MS)

        try:
            # Navigate
            try:
                page.goto(cfg.URL, wait_until="domcontentloaded")
                if proxy_server:
                    print(f"[PROXY OK] Loaded via proxy: {proxy_server}")

                    if cfg.ENABLE_PROXY_IP_CHECK:
                        observed_proxy_ip = _check_public_ip_via_proxy(context, cfg)
                        if observed_proxy_ip:
                            print(f"[PROXY IP] {observed_proxy_ip}")
                        else:
                            print("[PROXY IP] (unknown)")
                    else:
                        print("[PROXY IP] Skipped (ENABLE_PROXY_IP_CHECK=False)")

            except PlaywrightTimeoutError as e:
                if proxy_server:
                    proxy_load_error = True
                    print(f"⚠️ PROXY FAIL {proxy_server} → TIMEOUT")
                raise
            except Exception as e:
                if proxy_server and _is_proxyish_playwright_error(e):
                    proxy_load_error = True
                    print(f"⚠️ PROXY FAIL {proxy_server} → {_one_line_error(e)}")
                raise

            page.wait_for_selector(cfg.HEALTHCHECK_SELECTOR)
            page.wait_for_selector(cfg.POPUP_ROOT_SELECTOR, state="attached")

            # Region dropdown selection
            page.wait_for_selector(cfg.REGION_SELECT_SELECTOR)
            page.select_option(cfg.REGION_SELECT_SELECTOR, value=cfg.REGION_VALUE)
            selected = page.input_value(cfg.REGION_SELECT_SELECTOR)
            if selected != cfg.REGION_VALUE:
                raise RuntimeError(f"Region selection failed: expected '{cfg.REGION_VALUE}', got '{selected}'")

            # Fill member code
            page.wait_for_selector(cfg.MEMBER_CODE_SELECTOR)
            page.fill(cfg.MEMBER_CODE_SELECTOR, cfg.MEMBER_CODE_VALUE)
            member_val = page.input_value(cfg.MEMBER_CODE_SELECTOR)
            if member_val != cfg.MEMBER_CODE_VALUE:
                raise RuntimeError(f"Member code fill failed: expected '{cfg.MEMBER_CODE_VALUE}', got '{member_val}'")

            # Coupon code
            coupon_code_used = generate_coupon_code(rng)
            page.wait_for_selector(cfg.COUPON_CODE_SELECTOR)
            page.fill(cfg.COUPON_CODE_SELECTOR, coupon_code_used)
            coupon_val = page.input_value(cfg.COUPON_CODE_SELECTOR)
            if coupon_val != coupon_code_used:
                raise RuntimeError(f"Coupon code fill failed: expected '{coupon_code_used}', got '{coupon_val}'")

            # Click Redeem
            page.wait_for_selector(cfg.REDEEM_BUTTON_SELECTOR)
            page.click(cfg.REDEEM_BUTTON_SELECTOR)

            # Wait for popup to become "on"
            try:
                page.wait_for_selector(cfg.POPUP_ON_SELECTOR, timeout=cfg.POPUP_ON_WAIT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                shot = save_screenshot(page, cfg.SCREENSHOT_DIR, "popup_on_not_detected")
                msg = (
                    "Popup did not transition to 'on' state after clicking Redeem.\n"
                    f"Coupon: {coupon_code_used}\n"
                    f"Waited for selector: {cfg.POPUP_ON_SELECTOR}\n"
                    f"Screenshot: {shot}"
                )
                if proxy_server and observed_proxy_ip:
                    msg += f"\nProxy: {proxy_server}\nProxy IP: {observed_proxy_ip}"
                elif proxy_server:
                    msg += f"\nProxy: {proxy_server}\nProxy IP: (unknown)"

                if cfg.DISCORD_WEBHOOK_URL and cfg.ALWAYS_SEND_DISCORD:
                    send_discord(cfg.DISCORD_WEBHOOK_URL, f"⚠️ Popup not 'on'\n```{msg}```", shot)
                return False, msg, False, proxy_load_error

            popup_html = page.inner_html(cfg.POPUP_MESSAGE_SELECTOR).strip()
            shot = save_screenshot(page, cfg.SCREENSHOT_DIR, "popup_on")

            is_expected_failure = (popup_html == cfg.EXPECTED_POPUP_MESSAGE_HTML)

            if is_expected_failure:
                log_msg = (
                    "Popup detected (popAlert is 'on'). Redemption failed as expected (normal invalid-coupon message).\n"
                    f"Coupon: {coupon_code_used}\n"
                    f"Popup HTML: {popup_html}\n"
                    f"Screenshot: {shot}"
                )
            else:
                log_msg = (
                    "Popup detected (popAlert is 'on'). Result is UNEXPECTED (message differs).\n"
                    f"Coupon: {coupon_code_used}\n"
                    f"Popup HTML: {popup_html}\n"
                    f"Expected HTML: {cfg.EXPECTED_POPUP_MESSAGE_HTML}\n"
                    f"Screenshot: {shot}"
                )

            if proxy_server and observed_proxy_ip:
                log_msg += f"\nProxy: {proxy_server}\nProxy IP: {observed_proxy_ip}"
            elif proxy_server:
                log_msg += f"\nProxy: {proxy_server}\nProxy IP: (unknown)"

            # Discord behavior
            if cfg.DISCORD_WEBHOOK_URL:
                if cfg.ALWAYS_SEND_DISCORD:
                    header = "✅ Expected failure popup" if is_expected_failure else "🚨 Unexpected popup"
                    content = f"{header}\nCoupon: `{coupon_code_used}`\nPopup HTML: `{popup_html}`"
                    if proxy_server and observed_proxy_ip:
                        content += f"\nProxy: `{proxy_server}`\nProxy IP: `{observed_proxy_ip}`"
                    elif proxy_server:
                        content += f"\nProxy: `{proxy_server}`\nProxy IP: `(unknown)`"

                    if cfg.SEND_SCREENSHOT_ON_EVERY_RUN:
                        send_discord(cfg.DISCORD_WEBHOOK_URL, content, shot)
                    else:
                        send_discord(cfg.DISCORD_WEBHOOK_URL, content)
                else:
                    if not is_expected_failure:
                        content = (
                            "🚨 Unexpected coupon popup message\n"
                            f"Coupon: `{coupon_code_used}`\n"
                            f"Popup HTML: `{popup_html}`"
                        )
                        if proxy_server and observed_proxy_ip:
                            content += f"\nProxy: `{proxy_server}`\nProxy IP: `{observed_proxy_ip}`"
                        elif proxy_server:
                            content += f"\nProxy: `{proxy_server}`\nProxy IP: `(unknown)`"

                        send_discord(cfg.DISCORD_WEBHOOK_URL, content, shot)

            if (not is_expected_failure) and cfg.STOP_ON_UNEXPECTED:
                return False, log_msg, True, proxy_load_error

            return True, log_msg, False, proxy_load_error

        except Exception as e:
            # classify & suppress tracebacks for proxy-ish errors
            is_proxyish = proxy_server and (isinstance(e, PlaywrightTimeoutError) or _is_proxyish_playwright_error(e))
            if is_proxyish:
                proxy_load_error = True
                # Keep message one-line (no traceback spam)
                msg = f"⚠️ PROXY FAIL {proxy_server} → {_one_line_error(e)}"
                if cfg.ENABLE_PROXY_IP_CHECK and observed_proxy_ip:
                    msg += f" | IP={observed_proxy_ip}"
                return False, msg, False, proxy_load_error

            # Non-proxy errors
            if cfg.VERBOSE_NON_PROXY_ERRORS:
                err = f"Exception: {e!s}\n{traceback.format_exc()}"
            else:
                err = f"Exception: {_one_line_error(e)}"

            if proxy_server and cfg.ENABLE_PROXY_IP_CHECK:
                err += f"\nProxy: {proxy_server}\nProxy IP: {observed_proxy_ip or '(unknown)'}"

            if cfg.DISCORD_WEBHOOK_URL and cfg.ALWAYS_SEND_DISCORD:
                # Discord gets a trimmed traceback for debugging even if stdout is quiet
                dbg = f"Exception: {e!s}\n{traceback.format_exc()}"
                send_discord(cfg.DISCORD_WEBHOOK_URL, f"🚨 Exception\n```{dbg[:1800]}```")

            return False, err, False, proxy_load_error

        finally:
            context.close()
            browser.close()


def get_randomized_interval(
    base: int = CFG.INTERVAL_SECONDS,
    min_extra: int = CFG.RANDOM_INTERVAL_MIN,
    max_extra: int = CFG.RANDOM_INTERVAL_MAX,
) -> int:
    extra = random.randint(min_extra, max_extra)
    return base + extra


def main() -> None:
    run_count = 0

    while True:
        run_count += 1
        started = time.time()
        ok, msg, should_stop, proxy_load_error = run_once(CFG)
        elapsed = time.time() - started

        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        status = "OK" if ok else "FAIL"
        print(f"[{stamp}] run={run_count} status={status} elapsed={elapsed:.2f}s\n{msg}\n")

        if should_stop:
            print("Stopping script because STOP_ON_UNEXPECTED=True and an unexpected popup was detected.")
            raise SystemExit(2)

        if CFG.RUN_ONCE:
            break

        if proxy_load_error:
            print("Proxy/playwright load error detected — skipping interval wait and starting next run immediately.")
            continue

        wait_time = get_randomized_interval(CFG.INTERVAL_SECONDS)
        print(f"Waiting {wait_time} seconds before next run...")
        time.sleep(wait_time)


if __name__ == "__main__":
    main()
