"""Google Places verification for scraped dealer records."""

from __future__ import annotations

from dataclasses import fields, replace
import math
import os
import re
from typing import Callable, Iterable
from urllib.parse import quote_plus

import requests

from .schema import DealerRecord

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DEFAULT_SEARCH_TERMS = "building materials"
DEFAULT_TIMEOUT = 15


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _name_tokens(value: str) -> set[str]:
    stop_words = {
        "and",
        "the",
        "store",
        "stores",
        "dealer",
        "dealers",
        "distributor",
        "distributors",
        "authorized",
        "authorised",
        "private",
        "limited",
        "pvt",
        "ltd",
    }
    return {
        token
        for token in _clean_text(value).split()
        if len(token) > 1 and token not in stop_words
    }


def get_place_display_name(place: dict) -> str:
    display_name = place.get("displayName") or {}
    if isinstance(display_name, dict):
        return str(display_name.get("text") or "").strip()
    return str(display_name or "").strip()


def is_company_match(company_name: str, place_name: str) -> bool:
    """Return True only when the names match exactly, ignoring case/spacing."""
    return bool(_clean_text(company_name)) and _clean_text(company_name) == _clean_text(place_name)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def google_sort_score(rating, reviews) -> float:
    """Combined score for sorting only; it does not filter records."""
    rating_value = _safe_float(rating)
    review_count = _safe_int(reviews)
    if rating_value <= 0:
        return 0.0
    return round(rating_value * math.log10(review_count + 1), 4)


def _record_query(
    record: DealerRecord,
    *,
    city: str = "",
    pincode: str = "",
    state: str = "",
    search_terms: str = DEFAULT_SEARCH_TERMS,
) -> str:
    parts = [
        record.name,
        record.pincode or pincode,
        record.city or city,
        record.state or record.state_name or state,
        search_terms,
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def fetch_google_data(
    record: DealerRecord,
    *,
    api_key: str,
    city: str = "",
    pincode: str = "",
    state: str = "",
    search_terms: str = DEFAULT_SEARCH_TERMS,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | None:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.name,places.formattedAddress,"
            "places.nationalPhoneNumber,places.rating,places.userRatingCount,"
            "places.types,places.businessStatus,places.location"
        ),
    }
    body = {
        "textQuery": _record_query(
            record,
            city=city,
            pincode=pincode,
            state=state,
            search_terms=search_terms,
        ),
        "maxResultCount": 1,
    }

    response = requests.post(
        PLACES_SEARCH_URL,
        json=body,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    places = data.get("places") or []
    if not places:
        return None

    place = places[0]
    place_name = get_place_display_name(place)
    if not place_name or not is_company_match(record.name, place_name):
        return None

    rating = place.get("rating")
    reviews = place.get("userRatingCount")
    place_id = place.get("id") or ""
    directions_destination = place.get("formattedAddress") or place_name or record.name
    google_location = (
        "https://www.google.com/maps/search/?api=1"
        f"&query={quote_plus(str(directions_destination))}"
    )
    if place_id:
        google_location += f"&query_place_id={quote_plus(str(place_id))}"
    return {
        "google_name": place_name or place.get("name", ""),
        "google_full_address": place.get("formattedAddress", ""),
        "google_contact_number": place.get("nationalPhoneNumber", ""),
        "google_rating": round(_safe_float(rating)) if rating is not None else None,
        "google_reviews": reviews,
        "google_business_type": ", ".join(place.get("types", [])).replace("_", " "),
        "google_business_status": place.get("businessStatus", ""),
        "google_place_id": place_id or place.get("name", ""),
        "google_location": google_location,
        "google_score": google_sort_score(rating, reviews),
    }


def record_with_google_data(record: DealerRecord, google_data: dict | None) -> DealerRecord:
    if not google_data:
        return replace(record, google_verified=False)
    allowed_fields = {field.name for field in fields(record)}
    filtered_data = {
        key: value
        for key, value in google_data.items()
        if key in allowed_fields
    }
    return replace(record, google_verified=True, **filtered_data)


def is_operational_with_phone(record: DealerRecord) -> bool:
    return (
        str(record.google_business_status or "").casefold() == "operational"
        and bool(str(record.google_contact_number or "").strip())
    )


def sort_google_verified(records: Iterable[DealerRecord]) -> list[DealerRecord]:
    return sorted(
        records,
        key=lambda record: (
            _safe_float(record.google_score),
            _safe_float(record.google_rating),
            _safe_int(record.google_reviews),
        ),
        reverse=True,
    )


def verify_records_with_google(
    records: Iterable[DealerRecord],
    *,
    api_key: str | None = None,
    city: str = "",
    pincode: str = "",
    state: str = "",
    search_terms: str = DEFAULT_SEARCH_TERMS,
    timeout: int = DEFAULT_TIMEOUT,
    on_progress: Callable[[int, int, DealerRecord], None] | None = None,
) -> list[DealerRecord]:
    """Verify records, filter non-operational/no-phone Google matches, and sort."""
    resolved_api_key = (
        api_key
        or os.getenv("PLACES_API_KEY")
        or os.getenv("GOOGLE_PLACES_API_KEY")
        or os.getenv("GOOGLE_MAPS_API_KEY")
        or ""
    ).strip()
    if not resolved_api_key:
        raise RuntimeError(
            "Google Places API key is missing. Set PLACES_API_KEY in .env."
        )

    record_list = list(records)
    verified = []
    for index, record in enumerate(record_list, start=1):
        if on_progress:
            on_progress(index, len(record_list), record)
        try:
            google_data = fetch_google_data(
                record,
                api_key=resolved_api_key,
                city=city,
                pincode=pincode,
                state=state,
                search_terms=search_terms,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"[Google Places] {record.name}: {exc}")
            google_data = None
        enriched = record_with_google_data(record, google_data)
        if is_operational_with_phone(enriched):
            verified.append(enriched)

    return sort_google_verified(verified)
