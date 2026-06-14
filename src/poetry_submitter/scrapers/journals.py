"""
Scrape journal data from two sources:
1. Chill Subs magazine page (/magazine/{slug}) — structured guidelines, submission URL, formatting
2. The journal's own website — published poems for aesthetic comparison, fallback submission info
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page, async_playwright

from poetry_submitter.scrapers.chillsubs import _load_session

# Link text patterns that suggest a page has published poems/content to read
_READ_PATTERNS = re.compile(
    r"\b(read|issue|poem|poetry|fiction|prose|archive|current|latest|published|work)\b",
    re.IGNORECASE,
)
_SUBMIT_PATTERNS = re.compile(
    r"\b(submit|submissions?|guidelines?|contribute)\b",
    re.IGNORECASE,
)


@dataclass
class JournalProfile:
    # From Chill Subs structured data
    website: Optional[str]
    submission_url: Optional[str]
    submission_email: Optional[str]
    guidelines_url: Optional[str]
    guidelines_text: str
    accepts_simultaneous: bool
    accepts_pdf: bool
    accepts_docx: bool
    blind_submission: bool
    bio_max_words: Optional[int]
    bio_third_person: bool
    poetry_max_poems: Optional[int]
    poetry_max_pages: Optional[int]
    poetry_fee: Optional[str]
    open_call_descriptions: list[str]
    # From journal website
    aesthetic_notes: str           # vibe/about text
    poem_samples: list[str]        # excerpts of published poems


async def scrape_journal(chillsubs_slug: str, journal_website: Optional[str] = None) -> JournalProfile:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await _load_session(context)  # no-op if no session saved

        page = await context.new_page()

        # 1. Chill Subs magazine page for structured guidelines
        cs_url = f"https://www.chillsubs.com/magazine/{chillsubs_slug}"
        await page.goto(cs_url, wait_until="domcontentloaded", timeout=20_000)
        cs_data = await _extract_next_data(page)
        profile = _parse_cs_data(cs_data)

        if not profile.website and journal_website:
            profile.website = journal_website

        # 2. Journal's own website — aesthetic content + submission fallback
        if profile.website:
            journal_page = await context.new_page()
            try:
                await journal_page.goto(
                    profile.website, wait_until="domcontentloaded", timeout=20_000
                )
                aesthetic, samples = await _scrape_journal_site(journal_page, profile)
                profile.aesthetic_notes = aesthetic
                profile.poem_samples = samples

                # Fill in submission info not found on Chill Subs
                if not profile.submission_url and not profile.submission_email:
                    sub_url, sub_email = await _find_submission_info(
                        journal_page, profile.website
                    )
                    profile.submission_url = sub_url
                    profile.submission_email = sub_email
            except Exception:
                pass
            finally:
                await journal_page.close()

        await browser.close()

    return profile


async def _scrape_journal_site(
    page: Page, profile: JournalProfile
) -> tuple[str, list[str]]:
    """
    Extract aesthetic notes (about/description) and published poem samples
    from the journal's homepage, then follow promising links.
    """
    base_url = page.url.rstrip("/")
    aesthetic = await _extract_about_text(page)
    samples = await _find_poem_samples(page, base_url)
    return aesthetic, samples


async def _extract_about_text(page: Page) -> str:
    """Pull the about/description text from the homepage."""
    for selector in ["main", "article", ".about", ".description", "#about", "body"]:
        el = await page.query_selector(selector)
        if el:
            text = await el.inner_text()
            if len(text) > 100:
                lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 40]
                return " ".join(lines[:8])[:600]
    return ""


async def _find_poem_samples(page: Page, base_url: str) -> list[str]:
    """
    Follow links that look like they lead to published poems/issues.
    Returns up to 3 poem text excerpts.
    """
    samples: list[str] = []

    # Gather candidate links from the homepage
    links = await page.query_selector_all("a[href]")
    candidates: list[str] = []
    for link in links:
        href = (await link.get_attribute("href") or "").strip()
        text = (await link.inner_text()).strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        if _READ_PATTERNS.search(text) or _READ_PATTERNS.search(href):
            full = href if href.startswith("http") else base_url + "/" + href.lstrip("/")
            if full not in candidates:
                candidates.append(full)
        if len(candidates) >= 8:
            break

    # Visit candidates and try to extract poem-like content
    context = page.context
    for url in candidates[:4]:
        if len(samples) >= 3:
            break
        try:
            p = await context.new_page()
            await p.goto(url, wait_until="domcontentloaded", timeout=12_000)
            text = await _extract_poem_text(p)
            if text:
                samples.append(text)
            await p.close()
        except Exception:
            try:
                await p.close()
            except Exception:
                pass

    return samples


async def _extract_poem_text(page: Page) -> str:
    """
    Try to find poem-like content on a page: short lines, stanza breaks.
    Returns up to 300 chars if found.
    """
    for selector in ["article", "main", ".poem", ".content", ".entry-content", "body"]:
        el = await page.query_selector(selector)
        if not el:
            continue
        text = await el.inner_text()
        lines = [l for l in text.splitlines() if l.strip()]
        # Poems tend to have short lines — check average line length
        if len(lines) < 3:
            continue
        avg_len = sum(len(l) for l in lines[:20]) / min(len(lines), 20)
        if avg_len < 60:
            # Looks poem-like: short lines
            excerpt = "\n".join(lines[:15])
            return excerpt[:300]
    return ""


async def _find_submission_info(
    page: Page, base_url: str
) -> tuple[Optional[str], Optional[str]]:
    """
    If Chill Subs didn't give us a submission URL or email,
    look for one on the journal's own site.
    """
    links = await page.query_selector_all("a[href]")
    sub_page_url = None
    for link in links:
        href = (await link.get_attribute("href") or "").strip()
        text = (await link.inner_text()).strip()
        if _SUBMIT_PATTERNS.search(text) or _SUBMIT_PATTERNS.search(href):
            sub_page_url = (
                href if href.startswith("http") else base_url + "/" + href.lstrip("/")
            )
            break

    if not sub_page_url:
        return None, None

    try:
        context = page.context
        sub_page = await context.new_page()
        await sub_page.goto(sub_page_url, wait_until="domcontentloaded", timeout=12_000)
        body_text = await sub_page.inner_text("body")
        await sub_page.close()

        email_match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", body_text)
        email = email_match.group(0) if email_match else None

        submittable = re.search(r"https?://[^\s]*submittable\.com[^\s\"'<>]*", body_text)
        sub_url = submittable.group(0) if submittable else (sub_page_url if not email else None)

        return sub_url, email
    except Exception:
        return None, None


async def _extract_next_data(page: Page) -> dict:
    try:
        raw = await page.evaluate(
            "() => document.getElementById('__NEXT_DATA__')?.textContent"
        )
        if not raw:
            return {}
        data = json.loads(raw)
        return data.get("props", {}).get("pageProps", {}).get("listing", {})
    except Exception:
        return {}


def _parse_cs_data(listing: dict) -> JournalProfile:
    guidelines = listing.get("guidelines") or {}
    subs = guidelines.get("submissions") or {}
    fmt = guidelines.get("formatting") or {}
    cl = guidelines.get("coverLetter") or {}
    process = guidelines.get("process") or {}

    file_type = fmt.get("fileType") or {}
    accepts_pdf = file_type.get("pdf", False)
    accepts_docx = file_type.get("docx", False) or file_type.get("doc", False)

    bio_info = cl.get("thirdPersonBio") or {}
    bio_max_words = None
    if bio_info.get("maxWordCount"):
        wc = bio_info["maxWordCount"]
        if wc.get("under50"):
            bio_max_words = 50
        elif wc.get("under75"):
            bio_max_words = 75
        elif wc.get("under100"):
            bio_max_words = 100

    poetry_max_poems = None
    poetry_max_pages = None
    poetry_fee = None
    for genre in listing.get("genres") or []:
        if genre.get("value") == "poetry":
            poetry_max_poems = genre.get("max")
            poetry_max_pages = genre.get("maxLines") or genre.get("maximumPageCount")
            pay = genre.get("paymentInfo") or {}
            if pay.get("amount"):
                poetry_fee = (
                    f"{pay.get('currency', '')} {pay['amount']} "
                    f"{pay.get('type', '').replace('per', 'per ')}"
                ).strip()
            if genre.get("additional"):
                # include any extra notes
                pass
            break

    open_calls = []
    for call in listing.get("subCalls") or []:
        if call.get("status") != "open":
            continue
        genre_flags = call.get("genre") or {}
        if not genre_flags.get("poetry"):
            continue
        desc = call.get("description") or call.get("title") or ""
        open_calls.append(desc[:400])

    # Human-readable guidelines text for the AI
    parts = []
    if listing.get("descriptionFull"):
        parts.append(listing["descriptionFull"][:600])
    if listing.get("perfectCoverLetter"):
        parts.append(f"Cover letter: {listing['perfectCoverLetter']}")
    for genre in listing.get("genres") or []:
        if genre.get("value") == "poetry":
            if genre.get("additional"):
                parts.append(f"Poetry guidelines: {genre['additional']}")
            break
    if open_calls:
        parts.append("Active poetry call: " + open_calls[0][:300])

    return JournalProfile(
        website=listing.get("website"),
        submission_url=subs.get("submissionMethodAddress"),
        submission_email=None,
        guidelines_url=subs.get("guidelinesLink"),
        guidelines_text="\n\n".join(parts),
        accepts_simultaneous=subs.get("acceptsSimultaneousSubmissions", True),
        accepts_pdf=accepts_pdf,
        accepts_docx=accepts_docx,
        blind_submission=process.get("concealedSubmissions", False),
        bio_max_words=bio_max_words,
        bio_third_person=bio_info.get("hasThis", False),
        poetry_max_poems=poetry_max_poems,
        poetry_max_pages=poetry_max_pages,
        poetry_fee=poetry_fee,
        open_call_descriptions=open_calls,
        aesthetic_notes="",
        poem_samples=[],
    )
