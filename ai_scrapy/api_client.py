import os
import struct
import json
import time
import requests

from ai_scrapy.proto_utils import (build_search_request, _parse_search_response, SearchError)

_session = None


# ===================== HTTP 会话管理 =====================
def _has_captcha(text):
    from Ai_scrapy import CAPTCHA_KEYWORDS
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in CAPTCHA_KEYWORDS)


def _verify_session(s):
    from Ai_scrapy import TARGET_CLASS_CODE, SEARCH_URL, HEADERS
    try:
        proto_data = build_search_request(
            f"分类号:({TARGET_CLASS_CODE})", page=1, page_size=1, year="2025")
        frame = b"\x00" + struct.pack(">I", len(proto_data)) + proto_data
        resp = s.post(SEARCH_URL, headers=HEADERS, data=frame, timeout=15)
        if resp.status_code == 200 and resp.content and len(resp.content) >= 5 and resp.content[0] == 0:
            return True
    except Exception:
        pass
    return False


def get_session():
    from Ai_scrapy import COOKIE_FILE
    global _session
    if _session is not None:
        return _session

    _session = requests.Session()
    saved = load_cookies_from_file()
    if saved:
        for name, value in saved.items():
            _session.cookies.set(name, value, domain=".wanfangdata.com.cn")
        print(f"  [Session] Loaded {len(saved)} cookies from {COOKIE_FILE}")
        if _verify_session(_session):
            print("  [Session] Cookie verification passed")
            return _session
        print("  [Session] Saved cookies expired, creating new session...")

    return _create_session()


def _create_session():
    from Ai_scrapy import HEADERS
    global _session
    _session = requests.Session()
    _session.headers.update(HEADERS)

    print("  [Session] Creating new session (no valid cookies)...")
    try:
        resp = _session.get(
            "https://s.wanfangdata.com.cn/advanced-search/patent",
            timeout=15,
            headers=HEADERS,
        )
        print(f"  [Session] Initial GET returned {resp.status_code}")
    except Exception as e:
        print(f"  [Session] Warning: initial GET failed: {e}")

    save_cookies_to_file()
    return _session


def save_cookies_to_file():
    from Ai_scrapy import ensure_dir, COOKIE_FILE
    global _session
    ensure_dir()
    if _session is None:
        return
    try:
        cookie_dict = {c.name: c.value for c in _session.cookies if c.domain and "wanfang" in c.domain}
        if cookie_dict:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
            print(f"  [Session] Saved {len(cookie_dict)} cookies to {COOKIE_FILE}")
    except Exception as e:
        print(f"  [Session] Warning: failed to save cookies: {e}")


def load_cookies_from_file():
    from Ai_scrapy import COOKIE_FILE
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def reset_session():
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
    _session = None


# ===================== 登录浏览器 =====================
def open_login_browser():
    """打开浏览器让用户手动登入万方，完成后保存 cookie"""
    from Ai_scrapy import HEADERS, ensure_dir, COOKIE_FILE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [ERROR] playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    print("\n  Opening browser for login...")
    print("  Please log in manually on the Wanfang page.")
    print("  After login is complete, CLOSE the browser window to continue.")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=HEADERS["User-Agent"],
            )
            page = context.new_page()

            print("  [Browser] Opening Wanfang advanced search page...")
            page.goto("https://s.wanfangdata.com.cn/advanced-search/patent",
                      wait_until="domcontentloaded", timeout=30000)

            print("  [Browser] Waiting for you to log in and close the browser...")
            try:
                while not page.is_closed():
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
            except KeyboardInterrupt:
                pass

            try:
                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                ensure_dir()
                with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
                print(f"  [Browser] Saved {len(cookie_dict)} cookies to {COOKIE_FILE}")

                global _session
                if _session:
                    for name, value in cookie_dict.items():
                        _session.cookies.set(name, value, domain=".wanfangdata.com.cn")
            except Exception as ex:
                print(f"  [Browser] Cookie extraction failed: {ex}")

            browser.close()
    except Exception as ex:
        print(f"  [Browser] Error: {ex}")


# ===================== 反爬：手动滑块验证 =====================
def _open_captcha_browser(search_word):
    """打开浏览器让用户手动完成滑块验证，完成后提取 cookies"""
    from Ai_scrapy import ensure_dir, COOKIE_FILE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [ERROR] playwright not installed. Run: pip install playwright && playwright install chromium")
        time.sleep(30)
        return

    print("  Launching browser (visible mode)...")
    print("  Please manually solve the captcha slider if it appears.")
    print("  Then CLOSE the browser window to continue scraping.")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            print("  [Browser] Opening Wanfang advanced search...")
            page.goto("https://s.wanfangdata.com.cn/advanced-search/patent",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            try:
                popup = page.locator(".layui-layer-close").first
                if popup.count() > 0:
                    popup.click()
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            print("  [Browser] Switching to professional search...")
            try:
                page.click("span:text('专业检索')", timeout=10000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

            print(f"  [Browser] Filling search: {search_word}")
            try:
                textarea = page.locator("#queryInput textarea").first
                textarea.fill(search_word)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            print("  [Browser] Waiting for you to solve captcha and close the window...")
            try:
                while not page.is_closed():
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
            except KeyboardInterrupt:
                pass

            try:
                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                ensure_dir()
                with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
                print(f"  [Browser] Saved {len(cookie_dict)} cookies to {COOKIE_FILE}")

                global _session
                if _session:
                    for name, value in cookie_dict.items():
                        _session.cookies.set(name, value, domain=".wanfangdata.com.cn")
            except Exception as ex:
                print(f"  [Browser] Cookie extraction failed: {ex}")

            browser.close()
    except Exception as ex:
        print(f"  [Browser] Error: {ex}")
        print("  Falling back to 30s wait...")


# ===================== API 调用 =====================
def search_patents(search_word, page=1, page_size=20, year=None, country_code=None):
    from Ai_scrapy import SEARCH_URL, HEADERS
    session = get_session()
    proto_data = build_search_request(search_word, page, page_size, year=year, country_code=country_code)
    frame = b"\x00" + struct.pack(">I", len(proto_data)) + proto_data

    resp = session.post(SEARCH_URL, headers=HEADERS, data=frame, timeout=30)

    if resp.status_code != 200:
        body_preview = resp.text[:200] if resp.text else "(empty)"
        raise SearchError(f"HTTP {resp.status_code}: {body_preview}", retryable=True)

    raw = resp.content
    if not raw:
        raise SearchError("Empty response body", retryable=True)

    if len(raw) < 5 or raw[0] != 0:
        raise SearchError(f"Bad gRPC frame: len={len(raw)}, first_byte={raw[0] if raw else 'N/A'}", retryable=True)

    plen = struct.unpack(">I", raw[1:5])[0]
    if plen == 0 or plen > len(raw) - 5:
        raise SearchError(f"Invalid proto length: {plen}, raw_len={len(raw)}", retryable=True)

    proto = raw[5 : 5 + plen]
    total, items_raw = _parse_search_response(proto)
    return total, items_raw


def search_with_retry(search_word, page=1, page_size=20, year=None, country_code=None, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            return search_patents(search_word, page, page_size, year=year, country_code=country_code)
        except SearchError as e:
            print(f"  [Attempt {attempt}/{max_retries}] Search error: {e}")
            if not e.retryable:
                raise
            if attempt < max_retries:
                if "Empty response body" in str(e):
                    print("  === IP blocked, opening browser for manual captcha ===")
                    _open_captcha_browser(search_word)
                    print("  === Browser closed, retrying search... ===")
                else:
                    wait = 10 * (2 ** (attempt - 1))
                    print(f"  Waiting {wait}s for rate limit to expire...")
                    time.sleep(wait)
                reset_session()
            else:
                raise

