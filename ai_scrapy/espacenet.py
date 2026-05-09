import os
import sys
import re
import time


def _detect_browser():
    """自动检测可用的浏览器：Chrome > Edge。
    返回 (profile_path, channel, user_agent)
    用户需事先用该浏览器访问 espacenet.com 通过一次 Cloudflare。
    """
    browsers = [
        {
            "name": "Chrome",
            "profile": os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
            "channel": "chrome",
            "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
        },
        {
            "name": "Edge",
            "profile": os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
            "channel": "msedge",
            "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"),
        },
    ]

    for browser in browsers:
        path = browser["profile"]
        if os.path.exists(path):
            print(f"[Espacenet] Detected {browser['name']}: {path}")
            return path, browser["channel"], browser["ua"]

    print("[Espacenet] ERROR: Neither Chrome nor Edge profile found.")
    print("  Please install Chrome/Edge, open espacenet.com once to pass Cloudflare, then retry.")
    sys.exit(1)


def _get_espacenet_profile():
    """复制浏览器 Cookies 到临时目录（绕过 Cloudflare，不锁原浏览器）。
    只复制 Cloudflare 绕过所需的 Cookies + Network，不复制整个 profile。"""
    from Ai_scrapy import ESPACENET_PROFILE_DIR
    import shutil
    src_profile, _channel, _ua = _detect_browser()
    if not os.path.exists(ESPACENET_PROFILE_DIR):
        print("[Espacenet] Copying browser profile (Cookies only, one-time)...")
        os.makedirs(ESPACENET_PROFILE_DIR, exist_ok=True)

        default_src = os.path.join(src_profile, "Default")
        default_dst = os.path.join(ESPACENET_PROFILE_DIR, "Default")
        os.makedirs(default_dst, exist_ok=True)

        for name in ("Cookies", "Cookies-journal", "Network", "Preferences"):
            src = os.path.join(default_src, name)
            dst = os.path.join(default_dst, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)

        for fname in ("Local State", "Variations"):
            src = os.path.join(src_profile, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(ESPACENET_PROFILE_DIR, fname))

        print("[Espacenet] Profile copied.")
    return ESPACENET_PROFILE_DIR


def _parse_publication_number(pub_num):
    """解析公开号 → CC, NR, KC。如 US2025171144A1 → US, 2025171144A1, A1"""
    cc = pub_num[:2]
    rest = pub_num[2:]
    kc_match = re.search(r'([A-Z]\d*)$', rest)
    kc = kc_match.group(1) if kc_match else "A"
    nr = rest
    return cc, nr, kc


def _parse_espacenet_html(html):
    """从 Espacenet API HTML 中提取 IPC 和 CPC 分类号"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    result = {"ipc": [], "cpc": []}

    for td in soup.find_all("td", class_="containsTable"):
        table = td.find("table")
        if not table:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(" ", strip=True)
            if not value:
                continue
            if "international" in label:
                result["ipc"] = [v.strip() for v in value.split(";") if v.strip()]
            elif "cooperative" in label:
                for v in value.split(";"):
                    v = v.strip()
                    if v:
                        v_clean = re.sub(r'\s*\([^)]*\)', '', v).strip()
                        result["cpc"].append(v_clean)
    return result


class EspacenetClient:
    """用 Playwright + 浏览器配置文件调 Espacenet API（真实 TLS 绕过 Cloudflare）。
    自动检测 Chrome / Edge，无需手动配置。"""

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright

        _src_profile, channel, user_agent = _detect_browser()

        self._pw = sync_playwright().start()
        profile = _get_espacenet_profile()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=headless,
            channel=channel,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
        )
        self._page = None
        self._count = 0
        self._last_referer = None

    def _ensure_page(self):
        if self._page is None or self._page.is_closed():
            self._page = self._ctx.new_page()
        return self._page

    def fetch_classifications(self, pub_num):
        """根据公开号获取 Espacenet 分类号
        Returns: {"ipc": [...], "cpc": [...]} 或 None
        """
        try:
            cc, nr, kc = _parse_publication_number(pub_num)
        except Exception:
            return None

        page = self._ensure_page()
        rnd = int(time.time() * 1000)
        api_url = (
            f"https://worldwide.espacenet.com/data/publicationDetails/biblio"
            f"?CC={cc}&NR={nr}&KC={kc}&FT=D&ND=&date=&DB=&locale=&rnd={rnd}"
        )
        referer = f"https://worldwide.espacenet.com/publicationDetails/biblio?CC={cc}&NR={nr}&KC={kc}"

        try:
            if referer != self._last_referer:
                page.set_extra_http_headers({
                    "Accept": "text/html, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                })
                self._last_referer = referer

            response = page.goto(api_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  [Espacenet] goto/header error for {pub_num}: {e}")
            return None

        if not response or not response.ok:
            return None

        html = response.text()
        if "Just a moment" in html[:200]:
            print(f"  [Espacenet] Cloudflare challenge for {pub_num}, retrying after delay...")
            time.sleep(5)
            try:
                response = page.goto(api_url, wait_until="domcontentloaded", timeout=30000)
                if response and response.ok:
                    html = response.text()
                else:
                    return None
            except Exception:
                return None

        self._count += 1
        return _parse_espacenet_html(html)

    def close(self):
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
            self._ctx.close()
            self._pw.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
