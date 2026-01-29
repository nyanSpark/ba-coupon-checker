#!/usr/bin/env python3
"""
Automated Web Form Tester
Built on Python 3.13.7
Install:
  pip install playwright requests
  playwright install chromium
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
    URL: str = ""  # paste the url to check here here (for example: https://mcoupon.nexon.com/bluearchive)

    # Schedule
    RUN_ONCE: bool = False
    INTERVAL_SECONDS: int = 20

    # Browser behavior
    HEADLESS: bool = True
    NAVIGATION_TIMEOUT_MS: int = 30_000
    ACTION_TIMEOUT_MS: int = 10_000

    # Discord
    DISCORD_WEBHOOK_URL: str = ""  # paste webhook url here (for example: https://discord.com/api/webhooks/<number>/<big hash>)

    ALWAYS_SEND_DISCORD: bool = False
    SEND_SCREENSHOT_ON_EVERY_RUN: bool = False

    # Stop on unexpected popup message
    STOP_ON_UNEXPECTED: bool = True

    # Basic page check
    HEALTHCHECK_SELECTOR: str = "body"

    # Region dropdown selection
    REGION_SELECT_SELECTOR: str = "#eRedeemRegion"
    REGION_VALUE: str = "na"

    # Inputs
    MEMBER_CODE_SELECTOR: str = "#eRedeemNpaCode"
    MEMBER_CODE_VALUE: str = ""  # paste Member Code here: (for example: 0ZE12A98060BY)
    COUPON_CODE_SELECTOR: str = "#eRedeemCoupon"

    # Redeem button
    REDEEM_BUTTON_SELECTOR: str = "button.btn_confirm.e-characters-with-npacode[data-message='redeem']"

    # Popup specifics (key change: wait for #popAlert to become "on")
    POPUP_ROOT_SELECTOR: str = "#popAlert"
    POPUP_ON_SELECTOR: str = "#popAlert.pop.on"   # CSS selector that only matches once class "on" is present
    POPUP_MESSAGE_SELECTOR: str = "#popAlert p.pop_msg"

    # Expected “normal failure” popup message (HTML includes <br>)
    EXPECTED_POPUP_MESSAGE_HTML: str = (
        "The coupon cannot be used in this game.<br>Please check the coupon number again."
    )

    # Waits
    POPUP_ON_WAIT_TIMEOUT_MS: int = 12_000

    # Screenshot
    SCREENSHOT_DIR: str = "screenshots"


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


def run_once(cfg: Config) -> Tuple[bool, str, bool]:
    """
    Returns:
      (ok, message, should_stop)
    """
    rng = random.Random()
    coupon_code_used: Optional[str] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_navigation_timeout(cfg.NAVIGATION_TIMEOUT_MS)
        page.set_default_timeout(cfg.ACTION_TIMEOUT_MS)

        try:
            # Navigate
            page.goto(cfg.URL, wait_until="domcontentloaded")
            page.wait_for_selector(cfg.HEALTHCHECK_SELECTOR)

            # Ensure popup root exists (it exists even before clicking Redeem)
            page.wait_for_selector(cfg.POPUP_ROOT_SELECTOR, state="attached")

            # Select region + verify
            page.wait_for_selector(cfg.REGION_SELECT_SELECTOR)
            page.select_option(cfg.REGION_SELECT_SELECTOR, value=cfg.REGION_VALUE)
            selected = page.input_value(cfg.REGION_SELECT_SELECTOR)
            if selected != cfg.REGION_VALUE:
                raise RuntimeError(f"Region selection failed: expected '{cfg.REGION_VALUE}', got '{selected}'")

            # Fill member code + verify
            page.wait_for_selector(cfg.MEMBER_CODE_SELECTOR)
            page.fill(cfg.MEMBER_CODE_SELECTOR, cfg.MEMBER_CODE_VALUE)
            member_val = page.input_value(cfg.MEMBER_CODE_SELECTOR)
            if member_val != cfg.MEMBER_CODE_VALUE:
                raise RuntimeError(f"Member code fill failed: expected '{cfg.MEMBER_CODE_VALUE}', got '{member_val}'")

            # Generate + fill coupon code + verify
            coupon_code_used = generate_coupon_code(rng)
            page.wait_for_selector(cfg.COUPON_CODE_SELECTOR)
            page.fill(cfg.COUPON_CODE_SELECTOR, coupon_code_used)
            coupon_val = page.input_value(cfg.COUPON_CODE_SELECTOR)
            if coupon_val != coupon_code_used:
                raise RuntimeError(f"Coupon code fill failed: expected '{coupon_code_used}', got '{coupon_val}'")

            # Click Redeem
            page.wait_for_selector(cfg.REDEEM_BUTTON_SELECTOR)
            page.click(cfg.REDEEM_BUTTON_SELECTOR)

            # KEY CHANGE:
            # Wait until the popup becomes "active" by gaining the "on" class:
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
                if cfg.DISCORD_WEBHOOK_URL and cfg.ALWAYS_SEND_DISCORD:
                    send_discord(cfg.DISCORD_WEBHOOK_URL, f"⚠️ Popup not 'on'\n```{msg}```", shot)
                return False, msg, False

            # Now popup is active; read message HTML (preserve <br>)
            popup_html = page.inner_html(cfg.POPUP_MESSAGE_SELECTOR).strip()

            # Screenshot after popup is "on"
            shot = save_screenshot(page, cfg.SCREENSHOT_DIR, "popup_on")

            is_expected_failure = (popup_html == cfg.EXPECTED_POPUP_MESSAGE_HTML)

            # Clear log message
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

            # Discord behavior
            if cfg.DISCORD_WEBHOOK_URL:
                if cfg.ALWAYS_SEND_DISCORD:
                    header = "✅ Expected failure popup" if is_expected_failure else "🚨 Unexpected popup"
                    content = f"{header}\nCoupon: `{coupon_code_used}`\nPopup HTML: `{popup_html}`"
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
                        send_discord(cfg.DISCORD_WEBHOOK_URL, content, shot)

            # Stop on unexpected
            if (not is_expected_failure) and cfg.STOP_ON_UNEXPECTED:
                return False, log_msg, True

            return True, log_msg, False

        except Exception as e:
            err = f"Exception: {e!s}\n{traceback.format_exc()}"
            if cfg.DISCORD_WEBHOOK_URL and cfg.ALWAYS_SEND_DISCORD:
                send_discord(cfg.DISCORD_WEBHOOK_URL, f"🚨 Exception\n```{err[:1800]}```")
            return False, err, False

        finally:
            context.close()
            browser.close()

def get_randomized_interval(base: int = CFG.INTERVAL_SECONDS, min_extra: int = 10, max_extra: int = 50) -> int:
    """
    Returns base interval plus a random integer between min_extra and max_extra (inclusive).
    """
    extra = random.randint(min_extra, max_extra)
    return base + extra


def main() -> None:
    run_count = 0

    while True:
        run_count += 1
        started = time.time()
        ok, msg, should_stop = run_once(CFG)
        elapsed = time.time() - started

        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        status = "OK" if ok else "FAIL"
        print(f"[{stamp}] run={run_count} status={status} elapsed={elapsed:.2f}s\n{msg}\n")

        if should_stop:
            print("Stopping script because STOP_ON_UNEXPECTED=True and an unexpected popup was detected.")
            raise SystemExit(2)

        if CFG.RUN_ONCE:
            break

        wait_time = get_randomized_interval(CFG.INTERVAL_SECONDS)
        print(f"Waiting {wait_time} seconds before next run...")
        time.sleep(wait_time)


if __name__ == "__main__":
    main()

