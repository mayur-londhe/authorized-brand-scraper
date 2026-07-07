"""Headless IndiaMART directory scraper used by the Streamlit dashboard."""

from __future__ import annotations

import asyncio
import re
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit


CARD_SELECTOR = (
    "li.temp4-card, li.pCard1, li.dfd.grid-card, "
    "article.template7-product-card"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

EXTRACT_CARDS_SCRIPT = r"""
(selector) => [...document.querySelectorAll(selector)].map(card => {
    const text = (selector) => {
        const element = card.querySelector(selector);
        return element ? (element.innerText || '').trim() : '';
    };
    const link = (selector) => {
        const element = card.querySelector(selector);
        return element ? (element.href || element.getAttribute('href') || '') : '';
    };
    const companySelector = 'a.cncf1, a[class*="cncf"]';
    const productSelector = 'a.prdtitle, h2 a';
    const ratingText = text('.dag5, [class*="rating"]');
    const ratingMatch = ratingText.match(/\d+(?:\.\d+)?/);
    const allText = card.innerText || '';
    // IndiaMART commonly renders the count beside the rating as `4.3 (15)`.
    // Keep the labelled form as a fallback for alternate card templates.
    const bracketedReviewMatch = allText.match(
        /(?:^|\s)[0-5](?:\.\d+)?\s*\(\s*(\d[\d,]*)\s*\)/
    );
    const labelledReviewMatch = allText.match(
        /(\d[\d,]*)\s*(?:reviews?|ratings?)/i
    );
    const reviewMatch = bracketedReviewMatch || labelledReviewMatch;
    const trustText = [
        ...card.querySelectorAll('[class], [title], [aria-label], img')
    ].map(element => [
        element.className || '',
        element.title || '',
        element.getAttribute('aria-label') || '',
        element.alt || '',
        element.src || ''
    ].join(' ')).join(' ').toLowerCase();
    const price = text('.prc');
    const unit = text('.prcut');
    const responseMatch = allText.match(/\d+(?:\.\d+)?%\s*Response Rate/i);
    return {
        "Company Name": text(companySelector),
        "Product": text(productSelector),
        "Price": price ? price + (unit ? ' / ' + unit : '') : '',
        "Address": text('address span, address'),
        "Rating": ratingMatch ? Number(ratingMatch[0]) : null,
        "Review Count": reviewMatch
            ? Number(reviewMatch[1].replace(/,/g, ''))
            : 0,
        "Response Rate": responseMatch ? responseMatch[0] : '',
        "TrustSEAL": trustText.includes('trustseal')
            || trustText.includes('trust seal') ? 'Yes' : 'No',
        "GST Badge": trustText.includes('greengst') ? 'Yes' : 'No',
        "Company URL": link(companySelector)
    };
})
"""

EXTRACT_COMPANY_DETAILS_SCRIPT = r"""
() => {
    const company = (window.__COMPANY_DATA__ || {}).company || {};
    const text = selector => {
        const element = document.querySelector(selector);
        return element ? (element.innerText || element.textContent || '').trim() : '';
    };
    const ratingElement = document.querySelector(
        '.rating-container, [aria-label*="stars from"]'
    );
    const ratingLabel = ratingElement
        ? (ratingElement.getAttribute('aria-label') || '')
        : '';
    const ratingMatch = ratingLabel.match(/(\d+(?:\.\d+)?)\s+out of/i);
    const reviewMatch = ratingLabel.match(/from\s+(\d[\d,]*)\s+(?:votes?|reviews?)/i);
    return {
        contactPerson: text('.footer__contact-name'),
        address: text('.footer__address'),
        phone: String(company.pnsNumber || '').trim(),
        rating: company.seller_rating || (ratingMatch ? ratingMatch[1] : ''),
        reviews: company.rating_count || (reviewMatch ? reviewMatch[1] : ''),
        trustseal: Boolean(
            document.querySelector(
                'a[href*="/trustseal/"], .header-icon--trustseal, .ts-trigger'
            )
        ),
        gstVerified: Boolean(company.gst_verified_flag)
    };
}
"""


def normalize_product_slugs(values: str | Iterable[str]) -> list[str]:
    candidates = re.split(r"[\n,;]+", values) if isinstance(values, str) else values
    slugs: list[str] = []
    for value in candidates or []:
        slug = str(value).strip().casefold().split("?", 1)[0].rstrip("/")
        slug = slug.rsplit("/", 1)[-1]
        slug = re.sub(r"\.html?$", "", slug)
        slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def normalize_cities(values: str | Iterable[str]) -> list[str]:
    candidates = re.split(r"[\n,;]+", values) if isinstance(values, str) else values
    return list(dict.fromkeys(
        str(value).strip() for value in candidates or [] if str(value).strip()
    ))


def passes_quality_gate(record: dict) -> bool:
    return (
        record.get("TrustSEAL") == "Yes"
        or record.get("Trust Seal / GST Verified") == "Yes"
        or (
            int(record.get("Review Count") or 0) >= 5
            and float(record.get("Rating") or 0) >= 3.0
        )
    )


def select_quality_records(records: Iterable[dict]) -> list[dict]:
    """Keep sealed listings and listings meeting rating/review minimums."""
    return [record for record in records if passes_quality_gate(record)]


def product_dimension(product_name: str) -> str:
    value = str(product_name or "").strip()
    size_match = re.search(r"\bsize\s*:\s*(.+)$", value, re.IGNORECASE)
    if size_match:
        return size_match.group(1).strip(" ,-")
    if "," in value:
        return value.split(",", 1)[1].strip()
    return ""


def indiamart_enquiry_url(company_url: str) -> str:
    """Build IndiaMART's common company Contact Us endpoint."""
    try:
        parsed = urlsplit(str(company_url or "").strip())
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    if not hostname.endswith("indiamart.com") or not path_parts:
        return ""
    company_slug = path_parts[0]
    return urlunsplit((
        parsed.scheme or "https",
        parsed.netloc,
        f"/{company_slug}/enquiry.html",
        "",
        "",
    ))


def _text_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]


def _text_field(text: str, labels: str) -> str:
    """Read a labelled value from same-line or next-line visible text."""
    lines = _text_lines(text)
    pattern = re.compile(rf"^(?:{labels})\s*:?\s*(.*)$", re.IGNORECASE)
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip(" :-")
        if value:
            return value
        if index + 1 < len(lines):
            return lines[index + 1].strip(" :-")
    return ""


def _address_from_text(text: str) -> str:
    lines = _text_lines(text)
    label = re.compile(
        r"^(?:registered address|contact address|address)\s*:?\s*(.*)$",
        re.IGNORECASE,
    )
    stop = re.compile(
        r"^(?:contact person|contact number|mobile|phone|gst|email|website|"
        r"call us|send email|send sms|view additional details)\b",
        re.IGNORECASE,
    )
    noise = re.compile(
        r"^(?:get directions?|get direction|send email|send sms)$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = label.match(line)
        if not match:
            continue
        values = [match.group(1).strip(" :-")]
        for candidate in lines[index + 1:index + 5]:
            if stop.match(candidate):
                break
            if re.match(
                r"^.{2,80}(?:\([^()]{2,30}\)|\|\s*"
                r"(?:owner|proprietor|director|ceo|partner|manager))$",
                candidate,
                re.IGNORECASE,
            ):
                break
            if noise.match(candidate):
                continue
            values.append(candidate)
            if re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", candidate):
                break
        return ", ".join(value for value in values if value)
    return ""


def _contact_details_from_text(text: str) -> dict:
    lines = _text_lines(text)
    phone_match = re.search(
        r"(?<!\d)(?:(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}|"
        r"0\d{2,4}[\s-]?\d{6,8})(?!\d)",
        text,
    )
    gst_match = re.search(
        r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b",
        text.upper(),
    )
    address = _address_from_text(text)
    pin_match = re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", address or text)
    contact_person = _text_field(
        text,
        r"contact person|contact name|proprietor|owner|director",
    )
    role = _text_field(text, r"designation|role|job title")
    if not contact_person:
        person_candidate = next(
            (
                line for line in lines
                if re.match(
                    r"^.{2,80}(?:\([^()]{2,30}\)|\|\s*"
                    r"(?:owner|proprietor|director|ceo|partner|manager))$",
                    line,
                    re.IGNORECASE,
                )
            ),
            "",
        )
        contact_person = person_candidate

    person_role = re.match(
        r"^(.*?)\s*(?:\(([^()]+)\)|\|\s*([^|]+))\s*$",
        contact_person,
    )
    if person_role:
        contact_person = person_role.group(1).strip()
        role = role or (person_role.group(2) or person_role.group(3)).strip()

    if not address and contact_person:
        person_line_index = next(
            (
                index for index, line in enumerate(lines)
                if contact_person.casefold() in line.casefold()
            ),
            -1,
        )
        if person_line_index >= 0:
            address_lines = []
            for line in lines[person_line_index + 1:person_line_index + 7]:
                if re.match(
                    r"^(?:get directions?|send email|send sms|call us|"
                    r"contact number|view additional details)\b",
                    line,
                    re.IGNORECASE,
                ):
                    if address_lines:
                        break
                    continue
                if phone_match and phone_match.group(0) in line:
                    break
                address_lines.append(line)
                if re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", line):
                    break
            address = ", ".join(address_lines)
            pin_match = re.search(
                r"(?<!\d)[1-9]\d{5}(?!\d)",
                address or text,
            )
    verification_text = text.casefold()
    return {
        "Contact Person": contact_person,
        "Role": role,
        "Contact Number": (
            re.sub(r"[\s-]+", "", phone_match.group(0))
            if phone_match
            else _text_field(
                text,
                r"mobile(?: number)?|phone(?: number)?|contact number",
            )
        ),
        "Address": address,
        "Pin Location": pin_match.group(0) if pin_match else "",
        "GST": gst_match.group(0) if gst_match else "",
        "Trust Seal / GST Verified": (
            "Yes"
            if any(phrase in verification_text for phrase in (
                "trustseal verified",
                "trust seal verified",
                "trustseal",
                "trust seal",
                "gst verified",
            ))
            else "No"
        ),
    }


async def extract_contact_details(context, company_url: str) -> dict:
    details = {
        "Contact Person": "",
        "Role": "",
        "Contact Number": "",
        "Address": "",
        "Pin Location": "",
        "GST": "",
        "Trust Seal / GST Verified": "No",
        "Contact Extraction Status": "Not found",
    }
    if not company_url or not company_url.startswith("http"):
        return details

    page = await context.new_page()
    try:
        async def navigate(url: str):
            response = None
            for delay_ms in (0, 2_000, 5_000):
                if delay_ms:
                    await page.wait_for_timeout(delay_ms)
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=25_000,
                )
                if not response or response.status != 429:
                    return response
            return response

        async def capture_page() -> str:
            body = await page.locator("body").inner_text()
            structured = await page.evaluate(EXTRACT_COMPANY_DETAILS_SCRIPT)
            if structured.get("contactPerson"):
                person_details = _contact_details_from_text(
                    structured["contactPerson"]
                )
                if person_details["Contact Person"] and not details["Contact Person"]:
                    details["Contact Person"] = person_details["Contact Person"]
                if person_details["Role"] and not details["Role"]:
                    details["Role"] = person_details["Role"]
            if structured.get("address") and not details["Address"]:
                details["Address"] = structured["address"]
                pin_match = re.search(
                    r"(?<!\d)[1-9]\d{5}(?!\d)",
                    structured["address"],
                )
                if pin_match:
                    details["Pin Location"] = pin_match.group(0)
            if structured.get("phone") and not details["Contact Number"]:
                details["Contact Number"] = re.sub(
                    r"[\s-]+",
                    "",
                    structured["phone"],
                )
            if structured.get("trustseal") or structured.get("gstVerified"):
                details["Trust Seal / GST Verified"] = "Yes"
            if structured.get("rating") not in (None, ""):
                details["Rating"] = float(structured["rating"])
            if structured.get("reviews") not in (None, ""):
                details["Review Count"] = int(
                    str(structured["reviews"]).replace(",", "")
                )
            return body

        canonical_enquiry_url = indiamart_enquiry_url(company_url)
        initial_url = canonical_enquiry_url or company_url
        initial_response = await navigate(initial_url)
        if initial_response and initial_response.status == 429:
            details["Contact Extraction Status"] = "Rate limited (HTTP 429)"
            return details
        home_text = await capture_page()
        contact_links = await page.locator("a").evaluate_all(
            """links => links.map(link => ({
                text: (link.innerText || '').replace(/\\s+/g, ' ').trim(),
                href: link.href || ''
            }))"""
        )
        text_link_urls = [
            item["href"]
            for item in contact_links
            if item.get("href")
            and len(item.get("text", "")) <= 50
            and any(
                phrase in item.get("text", "").casefold()
                for phrase in (
                    "contact us",
                    "contact details",
                    "reach us",
                    "view contact",
                )
            )
        ]
        base = company_url.rstrip("/") + "/"
        candidate_urls = list(dict.fromkeys([
            urljoin(base, "enquiry.html"),
            *text_link_urls,
            urljoin(base, "contact-us.html"),
            urljoin(base, "contactus.html"),
        ]))
        candidate_urls = [
            url for url in candidate_urls
            if url and url.rstrip("/") != initial_url.rstrip("/")
        ]
        contact_texts = []
        if home_text and canonical_enquiry_url:
            contact_texts.append(home_text)

        # Custom IndiaMART storefront domains often expose TrustSEAL/phone data
        # on the homepage but keep the street address on /enquiry.html or a
        # contact page. Do not treat those partial homepage details as enough.
        needs_fallback = not details["Address"]
        for contact_url in candidate_urls if needs_fallback else []:
            try:
                response = await navigate(contact_url)
                if response and response.status == 429:
                    continue
                body = await capture_page()
                if body and body not in contact_texts:
                    contact_texts.append(body)
            except Exception:
                continue
        if home_text and not canonical_enquiry_url and home_text not in contact_texts:
            contact_texts.append(home_text)

        # Contact fields come from /enquiry.html first, then text-link fallbacks.
        for text in contact_texts:
            extracted = _contact_details_from_text(text)
            for field, value in extracted.items():
                if field == "Trust Seal / GST Verified":
                    if value == "Yes":
                        details[field] = "Yes"
                elif value and not details[field]:
                    details[field] = value

        # The company homepage is the authoritative fallback for GST and
        # TrustSEAL, while also filling any contact field absent everywhere else.
        home_details = _contact_details_from_text(home_text)
        for field, value in home_details.items():
            if field == "Trust Seal / GST Verified":
                if value == "Yes":
                    details[field] = "Yes"
            elif field == "GST":
                if value:
                    details[field] = value
            elif value and not details[field]:
                details[field] = value
        found_fields = sum(bool(details[field]) for field in (
            "Contact Person",
            "Contact Number",
            "Address",
            "GST",
        ))
        details["Contact Extraction Status"] = (
            "Extracted" if found_fields else "Not found"
        )
    except Exception as exc:
        details["Contact Extraction Status"] = f"Failed: {type(exc).__name__}"
    finally:
        await page.close()
    return details


async def scrape_indiamart(
    cities: str | Iterable[str],
    product_slugs: str | Iterable[str],
    *,
    apply_quality_gate: bool = True,
    max_scrolls: int = 30,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Scrape IndiaMART cards in a visible browser for debugging."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install project requirements and Chromium."
        ) from exc

    city_list = normalize_cities(cities)
    slug_list = normalize_product_slugs(product_slugs)
    if not city_list:
        raise ValueError("Enter at least one city.")
    if not slug_list:
        raise ValueError("Enter at least one product slug.")

    searches = [(city, slug) for slug in slug_list for city in city_list]
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            slow_mo=250,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
        )
        page = await context.new_page()
        try:
            for index, (city, slug) in enumerate(searches, start=1):
                label = f"{city} / {slug}"
                if on_progress:
                    on_progress(index - 1, len(searches), label)
                city_slug = re.sub(r"[^a-z0-9]+", "-", city.casefold()).strip("-")
                url = f"https://dir.indiamart.com/{city_slug}/{slug}.html"
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    await page.wait_for_selector(CARD_SELECTOR, timeout=15_000)
                except Exception:
                    if on_progress:
                        on_progress(index, len(searches), f"{label}: no listings")
                    continue

                previous_count = 0
                unchanged = 0
                for _ in range(max_scrolls):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(900)
                    count = await page.locator(CARD_SELECTOR).count()
                    unchanged = unchanged + 1 if count == previous_count else 0
                    previous_count = count
                    if unchanged >= 3:
                        break

                found = await page.evaluate(EXTRACT_CARDS_SCRIPT, CARD_SELECTOR)
                candidates = []
                for record in found:
                    record["City"] = city
                    record["Subcategory"] = slug.replace("-", " ").title()
                    key = (
                        re.sub(
                            r"\s+",
                            " ",
                            str(record.get("Company Name", "")).strip().casefold(),
                        ),
                        str(record.get("Company URL", ""))
                        .strip()
                        .casefold()
                        .rstrip("/"),
                    )
                    if not key[0] or key in seen:
                        continue
                    candidates.append((key, record))

                # IndiaMART rate-limits concurrent company-page navigation.
                semaphore = asyncio.Semaphore(1)

                async def enrich_contact(record):
                    async with semaphore:
                        details = await extract_contact_details(
                            context,
                            str(record.get("Company URL", "")),
                        )
                        await asyncio.sleep(0.75)
                    listing_address = record.get("Address", "")
                    record.update(details)
                    if not record.get("Address"):
                        record["Address"] = listing_address
                    if (
                        details["Trust Seal / GST Verified"] == "Yes"
                        or record.get("TrustSEAL") == "Yes"
                        or record.get("GST Badge") == "Yes"
                    ):
                        record["Trust Seal / GST Verified"] = "Yes"
                    record["Product Dimension / Size"] = product_dimension(
                        record.get("Product", "")
                    )
                    record.pop("Product", None)
                    record.pop("Company URL", None)
                    record.pop("TrustSEAL", None)
                    record.pop("GST Badge", None)
                    return record

                enriched = await asyncio.gather(*(
                    enrich_contact(record) for _, record in candidates
                ))
                for (key, _), record in zip(candidates, enriched):
                    if apply_quality_gate and not passes_quality_gate(record):
                        continue
                    seen.add(key)
                    records.append(record)
                if on_progress:
                    on_progress(index, len(searches), label)
        finally:
            await context.close()
            await browser.close()
    return records


def run_indiamart_scrape(*args, **kwargs) -> list[dict]:
    """Run the async scraper from Streamlit's worker thread."""
    return asyncio.run(scrape_indiamart(*args, **kwargs))
