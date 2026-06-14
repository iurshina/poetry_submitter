"""Playwright-based scraper for Chill Subs open calls."""
import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, async_playwright, BrowserContext

SESSION_PATH = Path(__file__).parent.parent.parent.parent / "data" / "chillsubs_session.json"
BROWSE_URL = "https://www.chillsubs.com/browse/magazines"


@dataclass
class OpenCallResult:
    journal_name: str
    journal_url: str
    chillsubs_id: str       # slug, e.g. "prism"
    closes: Optional[date]
    genre: str
    simultaneous_submissions: bool
    response_time_days: Optional[int]
    submission_url: Optional[str]


def import_cookies_from_file(cookies_txt: Path) -> None:
    """
    Import cookies from a Netscape-format cookies.txt file
    (exported via the 'Get cookies.txt LOCALLY' browser extension).
    Run once with: uv run poetry-sub chillsubs-import-cookies <file>
    """
    SESSION_PATH.parent.mkdir(exist_ok=True)
    cookies = []
    for line in cookies_txt.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path, secure, expires, name, value = parts[:7]
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip("."),
            "path": path,
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
            "sameSite": "Lax",
            **({"expires": int(expires)} if expires.isdigit() else {}),
        })
    SESSION_PATH.write_text(json.dumps(cookies, indent=2))
    print(f"Imported {len(cookies)} cookies → {SESSION_PATH}")


async def _load_session(context: BrowserContext) -> None:
    """Load saved cookies if available (optional — browse page is public)."""
    if SESSION_PATH.exists():
        cookies = json.loads(SESSION_PATH.read_text())
        await context.add_cookies(cookies)


async def scrape_open_calls(genre_filter: str = "poetry") -> list[OpenCallResult]:
    """
    Load the public browse page, apply Open + Poetry filters, collect all results
    from the embedded Next.js JSON and from subsequent API responses while scrolling.
    No login required — the Chill Subs browse page is publicly accessible.
    """
    all_items: list[dict] = []
    seen_ids: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await _load_session(context)  # loads cookies if available, no-op if not
        page = await context.new_page()

        # Intercept browse API responses so we capture paginated results
        async def capture_api(response):
            if "/api/browse" in response.url or "/browse/magazines" in response.url:
                try:
                    body = await response.json()
                    items = (
                        body.get("browseData")
                        or body.get("data")
                        or body.get("magazines")
                        or []
                    )
                    for item in items:
                        if item.get("id") not in seen_ids:
                            seen_ids.add(item["id"])
                            all_items.append(item)
                except Exception:
                    pass

        page.on("response", lambda r: asyncio.ensure_future(capture_api(r)))

        await page.goto(BROWSE_URL, wait_until="domcontentloaded")

        # Extract the SSR-embedded data from __NEXT_DATA__
        initial = await _extract_next_data(page)
        for item in initial:
            if item.get("id") not in seen_ids:
                seen_ids.add(item["id"])
                all_items.append(item)

        # Apply filters: Open + Poetry
        await _apply_filters(page, genre_filter)

        # Scroll to trigger pagination
        await _scroll_to_end(page)

        await browser.close()

    # Filter to journals that have the requested genre currently open
    results = []
    for item in all_items:
        open_genres = item.get("openGenres") or []
        if genre_filter not in open_genres:
            continue
        results.append(_item_to_result(item, genre_filter))

    return results


async def _extract_next_data(page: Page) -> list[dict]:
    """Pull the browseData array from the embedded __NEXT_DATA__ JSON."""
    try:
        raw = await page.evaluate(
            "() => document.getElementById('__NEXT_DATA__')?.textContent"
        )
        if not raw:
            return []
        data = json.loads(raw)
        return (
            data.get("props", {})
                .get("pageProps", {})
                .get("browseData", [])
        )
    except Exception:
        return []


async def _apply_filters(page: Page, genre: str) -> None:
    """Click the Open quick-filter and then select the genre."""
    # Click "Open" quick filter
    try:
        open_btn = page.get_by_role("button", name="Open").first
        await open_btn.click()
        await asyncio.sleep(1)
    except Exception:
        pass

    # Click the Genre dropdown button
    try:
        genre_btn = page.get_by_role("button", name="Genre").first
        await genre_btn.click()
        await asyncio.sleep(0.5)
        # Click the Poetry option inside the dropdown
        poetry_opt = page.get_by_role("option", name=genre.title())
        if not await poetry_opt.count():
            poetry_opt = page.get_by_text(genre.title(), exact=True)
        await poetry_opt.first.click()
        await asyncio.sleep(1.5)
    except Exception:
        pass


async def _scroll_to_end(page: Page, max_scrolls: int = 30) -> None:
    """Scroll until no new content loads."""
    prev_height = 0
    for _ in range(max_scrolls):
        height = await page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)


def _item_to_result(item: dict, genre: str) -> OpenCallResult:
    key = item.get("key", "")
    closes_ts = item.get("deadlineSortKey")
    closes = None
    if closes_ts:
        try:
            closes = datetime.fromtimestamp(closes_ts / 1000).date()
        except Exception:
            pass

    return OpenCallResult(
        journal_name=item.get("name", ""),
        journal_url=f"https://www.chillsubs.com/magazine/{key}",
        chillsubs_id=key,
        closes=closes,
        genre=genre,
        simultaneous_submissions=True,   # not in browse data; checked per-journal
        response_time_days=item.get("responseTimeDays") or None,
        submission_url=None,
    )
