"""Google Places verification for scraped dealer records."""

from __future__ import annotations

from dataclasses import fields, replace
from difflib import SequenceMatcher
import math
import os
import re
from typing import Callable, Iterable
from urllib.parse import quote_plus

import requests

from .schema import DealerRecord

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DEFAULT_SEARCH_TERMS = "building materials"
DEFAULT_TIMEOUT = 15
MIN_NAME_MATCH_SCORE = 0.62
STANDARD_DISTANCE_KM = 20.0
CITY_ONLY_DISTANCE_KM = 10.0
COARSE_ANCHOR_DISTANCE_KM = 25.0
PREFERRED_RATING = 3.5
PREFERRED_REVIEWS = 5
GENERIC_COMPANY_WORDS = {
    "building",
    "buildings",
    "material",
    "materials",
    "supplier",
    "suppliers",
    "supply",
    "shop",
    "shoppee",
    "store",
    "stores",
    "trader",
    "traders",
    "trading",
    "enterprise",
    "enterprises",
    "electrical",
    "electricals",
    "electronic",
    "electronics",
    "hardware",
    "light",
    "lights",
    "appliance",
    "appliances",
    "marketing",
    "company",
    "co",
    "india",
}


def _clean_text(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()),
    ).strip()


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
        "sub",
        "branch",
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


def _distinctive_tokens(value: str) -> set[str]:
    return {
        token for token in _name_tokens(value)
        if token not in GENERIC_COMPANY_WORDS
    }


def _best_token_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return max(
        SequenceMatcher(None, left_token, right_token).ratio()
        for left_token in left
        for right_token in right
    )


def get_place_display_name(place: dict) -> str:
    display_name = place.get("displayName") or {}
    if isinstance(display_name, dict):
        return str(display_name.get("text") or "").strip()
    return str(display_name or "").strip()


def company_name_score(company_name: str, place_name: str) -> float:
    """Score spelling similarity and shared significant words from 0 to 1."""
    company = _clean_text(company_name)
    place = _clean_text(place_name)
    if not company or not place:
        return 0.0
    company_tokens = _name_tokens(company)
    place_tokens = _name_tokens(place)
    token_score = (
        len(company_tokens & place_tokens) / len(company_tokens | place_tokens)
        if company_tokens and place_tokens
        else 0.0
    )
    sequence_score = SequenceMatcher(None, company, place).ratio()
    compact_sequence_score = SequenceMatcher(
        None,
        company.replace(" ", ""),
        place.replace(" ", ""),
    ).ratio()
    containment_score = 1.0 if company in place or place in company else 0.0
    raw_score = max(
        sequence_score,
        compact_sequence_score,
        0.65 * token_score + 0.35 * sequence_score,
        0.85 * containment_score + 0.15 * sequence_score,
    )

    company_distinctive = _distinctive_tokens(company)
    place_distinctive = _distinctive_tokens(place)
    distinctive_score = _best_token_similarity(
        company_distinctive,
        place_distinctive,
    )
    if compact_sequence_score >= 0.85:
        return round(raw_score, 4)
    if company_distinctive and place_distinctive and distinctive_score < 0.72:
        return round(min(raw_score, 0.45), 4)
    if not company_distinctive or not place_distinctive:
        return round(raw_score if raw_score >= 0.90 else min(raw_score, 0.55), 4)
    return round(0.8 * raw_score + 0.2 * distinctive_score, 4)


def is_company_match(company_name: str, place_name: str) -> bool:
    return company_name_score(company_name, place_name) >= MIN_NAME_MATCH_SCORE


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


def _resolved_api_key(api_key: str | None = None) -> str:
    resolved = (
        api_key
        or os.getenv("PLACES_API_KEY")
        or os.getenv("GOOGLE_PLACES_API_KEY")
        or os.getenv("GOOGLE_MAPS_API_KEY")
        or ""
    ).strip()
    if not resolved:
        raise RuntimeError(
            "Google Places API key is missing. Set PLACES_API_KEY in .env."
        )
    return resolved


def _coordinates(latitude, longitude) -> tuple[float, float] | None:
    try:
        lat, lng = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(math.sqrt(value))


def _geocode(query: str, *, api_key: str, timeout: int) -> dict | None:
    response = requests.get(
        GEOCODING_URL,
        params={"address": query, "key": api_key, "region": "in"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    status = str(payload.get("status") or "")
    if status not in {"OK", "ZERO_RESULTS"}:
        message = str(payload.get("error_message") or status or "Unknown error")
        raise RuntimeError(f"Geocoding API {status}: {message}")
    results = payload.get("results") or []
    return results[0] if results else None


def _geocode_coordinates(result: dict | None) -> tuple[float, float] | None:
    location = ((result or {}).get("geometry") or {}).get("location") or {}
    return _coordinates(location.get("lat"), location.get("lng"))


def _place_coordinates(place: dict) -> tuple[float, float] | None:
    location = place.get("location") or {}
    return _coordinates(location.get("latitude"), location.get("longitude"))


def _address_component(place: dict, component_type: str) -> str:
    for component in place.get("addressComponents") or []:
        if component_type in (component.get("types") or []):
            return str(
                component.get("longText")
                or component.get("shortText")
                or ""
            ).strip()
    return ""


def _pincode(value: str) -> str:
    match = re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", str(value or ""))
    return match.group(0) if match else ""


def fetch_places_by_product_locality(
    product: str,
    locality: str,
    *,
    api_key: str | None = None,
    max_results: int = 20,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch operational Google Places for a free-text product/locality search."""
    product = str(product or "").strip()
    locality = str(locality or "").strip()
    if not product:
        raise ValueError("Enter a product.")
    if not locality:
        raise ValueError("Enter a locality or city.")

    resolved_api_key = _resolved_api_key(api_key)
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": resolved_api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.addressComponents,places.nationalPhoneNumber,"
            "places.internationalPhoneNumber,places.rating,"
            "places.userRatingCount,places.types,places.businessStatus,"
            "places.location,places.websiteUri,places.googleMapsUri"
        ),
    }
    body = {
        "textQuery": f"{product} {locality} India",
        "maxResultCount": max(1, min(int(max_results or 20), 20)),
        "regionCode": "IN",
    }
    response = requests.post(
        PLACES_SEARCH_URL,
        json=body,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()

    rows = []
    for place in response.json().get("places") or []:
        phone = str(
            place.get("nationalPhoneNumber")
            or place.get("internationalPhoneNumber")
            or ""
        ).strip()
        if not phone:
            continue
        status = str(place.get("businessStatus") or "").strip()
        if status.casefold() != "operational":
            continue

        formatted_address = str(place.get("formattedAddress") or "").strip()
        coordinates = _place_coordinates(place)
        rating = place.get("rating")
        reviews = place.get("userRatingCount")
        rows.append({
            "Product": product,
            "Locality": locality,
            "Place Name": get_place_display_name(place),
            "Phone": phone,
            "Address": formatted_address,
            "Pincode": (
                _address_component(place, "postal_code")
                or _pincode(formatted_address)
            ),
            "City": (
                _address_component(place, "locality")
                or _address_component(place, "administrative_area_level_2")
            ),
            "State": _address_component(place, "administrative_area_level_1"),
            "Rating": round(_safe_float(rating), 1) if rating is not None else None,
            "Review Count": reviews,
            "Business Status": status,
            "Business Type": ", ".join(place.get("types", [])).replace("_", " "),
            "Google Maps": (
                place.get("googleMapsUri")
                or (
                    f"https://www.google.com/maps?q={coordinates[0]},{coordinates[1]}"
                    if coordinates else ""
                )
            ),
            "Website": place.get("websiteUri", ""),
            "Place ID": place.get("id", ""),
            "Latitude": coordinates[0] if coordinates else "",
            "Longitude": coordinates[1] if coordinates else "",
            "Google Score": google_sort_score(rating, reviews),
        })

    return sorted(
        rows,
        key=lambda row: (
            -_safe_float(row.get("Google Score")),
            -_safe_float(row.get("Rating")),
            -_safe_int(row.get("Review Count")),
            str(row.get("Place Name") or ""),
        ),
    )


def _address_tokens(value: str) -> set[str]:
    replacements = {
        "rd": "road",
        "st": "street",
        "nagar": "nagar",
        "main": "main",
    }
    ignored = {
        "india", "karnataka", "road", "street", "building", "shop",
        "floor", "near", "opposite", "opp", "district", "taluk",
        "complex", "layout", "colony", "ward", "cross", "main",
        "extension", "extn", "beside", "ground", "first", "second",
        "third", "number", "no",
    }
    cleaned = _clean_text(value)
    cleaned = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1\2", cleaned)
    tokens = []
    for token in cleaned.split():
        token = replacements.get(token, token)
        if len(token) > 1 and token not in ignored and not token.isdigit():
            tokens.append(token)
    return set(tokens)


def _city_only_address(record: DealerRecord) -> bool:
    source_tokens = _address_tokens(record.address)
    location_tokens = _address_tokens(
        " ".join((record.city, record.state, record.state_name, record.pincode))
    )
    return not (source_tokens - location_tokens)


def _address_overlap(record: DealerRecord, google_address: str) -> bool:
    source_tokens = _address_tokens(record.address)
    location_tokens = _address_tokens(
        " ".join((record.city, record.state, record.state_name))
    )
    significant = source_tokens - location_tokens
    google_tokens = _address_tokens(google_address)
    if record.pincode and record.pincode in google_address:
        return True
    return bool(significant & google_tokens)


def _brand_anchor(
    record: DealerRecord,
    *,
    api_key: str,
    city: str,
    state: str,
    timeout: int,
) -> tuple[tuple[float, float] | None, list[str]]:
    notes = []
    direct = _coordinates(record.latitude, record.longitude)
    if direct:
        return direct, ["coordinate-anchor"]

    resolved_city = record.city or city
    resolved_state = record.state or record.state_name or state
    if record.pincode:
        result = _geocode(
            f"{record.pincode} {resolved_city} {resolved_state} India",
            api_key=api_key,
            timeout=timeout,
        )
        pincode_anchor = _geocode_coordinates(result)
        if pincode_anchor:
            return pincode_anchor, ["pincode-centroid-anchor"]

    if str(record.address or "").strip():
        road_query = " ".join(filter(None, (
            record.address,
            resolved_city,
            resolved_state,
            "India",
        )))
        road_anchor = _geocode_coordinates(_geocode(
            road_query,
            api_key=api_key,
            timeout=timeout,
        ))
    else:
        road_anchor = None

    city_query = " ".join(filter(None, (resolved_city, resolved_state, "India")))
    city_anchor = _geocode_coordinates(_geocode(
        city_query,
        api_key=api_key,
        timeout=timeout,
    ))
    if road_anchor and city_anchor:
        if _haversine_km(road_anchor, city_anchor) > COARSE_ANCHOR_DISTANCE_KM:
            return city_anchor, ["city-centroid-anchor", "coarse-anchor"]
        return road_anchor, ["road-text-anchor"]
    if road_anchor:
        return road_anchor, ["road-text-anchor"]
    return city_anchor, ["city-centroid-anchor", "coarse-anchor"]


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
) -> tuple[dict | None, str]:
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
        "maxResultCount": 5,
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
        return None, "No Google Places result"

    scored_places = sorted(
        (
            (company_name_score(record.name, get_place_display_name(place)), place)
            for place in places
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    match_score, place = scored_places[0]
    place_name = get_place_display_name(place)
    if not place_name or match_score < MIN_NAME_MATCH_SCORE:
        return None, (
            f"Name mismatch ({match_score:.0%}): "
            f"{record.name} vs {place_name or 'N/A'}"
        )

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
    google_data = {
        "google_name": place_name or place.get("name", ""),
        "google_full_address": place.get("formattedAddress", ""),
        "google_contact_number": place.get("nationalPhoneNumber", ""),
        "google_rating": round(_safe_float(rating), 1) if rating is not None else None,
        "google_reviews": reviews,
        "google_business_type": ", ".join(place.get("types", [])).replace("_", " "),
        "google_business_status": place.get("businessStatus", ""),
        "google_place_id": place_id or place.get("name", ""),
        "google_location": google_location,
        "google_name_match_score": round(match_score * 100, 1),
        "google_score": google_sort_score(rating, reviews),
    }
    if str(google_data["google_business_status"] or "").casefold() != "operational":
        return google_data, "Google business is not operational"
    return google_data, ""


def fetch_brand_google_data_v2(
    record: DealerRecord,
    *,
    api_key: str,
    city: str = "",
    state: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict | None, str]:
    """Verify a brand dealer using an independent geographic anchor."""
    anchor, anchor_notes = _brand_anchor(
        record,
        api_key=api_key,
        city=city,
        state=state,
        timeout=timeout,
    )
    if not anchor:
        return None, "No usable geographic anchor"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.name,places.formattedAddress,"
            "places.addressComponents,places.nationalPhoneNumber,places.rating,"
            "places.userRatingCount,places.types,places.businessStatus,"
            "places.location"
        ),
    }
    body = {
        "textQuery": record.name,
        "maxResultCount": 10,
        "locationBias": {
            "circle": {
                "center": {
                    "latitude": anchor[0],
                    "longitude": anchor[1],
                },
                "radius": STANDARD_DISTANCE_KM * 1000,
            }
        },
    }
    response = requests.post(
        PLACES_SEARCH_URL,
        json=body,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    places = response.json().get("places") or []
    if not places:
        return None, "No Google Places result near anchor"

    city_only = _city_only_address(record)
    distance_limit = CITY_ONLY_DISTANCE_KM if city_only else STANDARD_DISTANCE_KM
    candidates = []
    for place in places:
        place_location = _place_coordinates(place)
        distance = (
            _haversine_km(anchor, place_location)
            if place_location
            else math.inf
        )
        place_name = get_place_display_name(place)
        name_score = company_name_score(record.name, place_name)
        address_pass = (
            True
            if city_only
            else _address_overlap(record, place.get("formattedAddress", ""))
        )
        distance_pass = distance <= distance_limit
        name_pass = name_score >= MIN_NAME_MATCH_SCORE
        candidates.append((
            int(distance_pass) + int(address_pass) + int(name_pass),
            name_score,
            -distance,
            place,
            distance,
            distance_pass,
            address_pass,
            name_pass,
        ))
    (
        _,
        match_score,
        _,
        place,
        distance,
        distance_pass,
        address_pass,
        name_pass,
    ) = max(candidates, key=lambda item: item[:3])

    place_name = get_place_display_name(place)
    formatted_address = str(place.get("formattedAddress") or "")
    place_location = _place_coordinates(place)
    place_id = place.get("id") or ""
    pincode = (
        _address_component(place, "postal_code")
        or _pincode(formatted_address)
    )
    matched_city = (
        _address_component(place, "locality")
        or _address_component(place, "administrative_area_level_2")
        or record.city
        or city
    )
    matched_state = (
        _address_component(place, "administrative_area_level_1")
        or record.state
        or record.state_name
        or state
    )
    google_location = (
        "https://www.google.com/maps/search/?api=1"
        f"&query={quote_plus(formatted_address or place_name)}"
    )
    if place_id:
        google_location += f"&query_place_id={quote_plus(str(place_id))}"

    checks = [
        f"distance {'pass' if distance_pass else 'fail'} "
        f"({distance:.2f}km/{distance_limit:.0f}km)",
        "address skipped (city-only)" if city_only else (
            f"address {'pass' if address_pass else 'fail'}"
        ),
        f"name {'pass' if name_pass else 'fail'} ({match_score:.0%})",
    ]
    notes = "; ".join([*anchor_notes, *checks])
    failure_reasons = []
    if not distance_pass:
        failure_reasons.append(
            f"Distance failed ({distance:.2f}km > {distance_limit:.0f}km)"
        )
    if not address_pass:
        failure_reasons.append("Address overlap failed")
    if not name_pass:
        failure_reasons.append(
            f"Name mismatch ({match_score:.0%}): "
            f"{record.name} vs {place_name or 'N/A'}"
        )
    operational = str(place.get("businessStatus") or "").casefold() == "operational"
    if not operational:
        failure_reasons.append("Google business is not operational")
    if failure_reasons:
        notes += "; rejected: " + "; ".join(failure_reasons)

    rating = place.get("rating")
    reviews = place.get("userRatingCount")
    google_data = {
        "google_name": place_name,
        "google_full_address": formatted_address,
        "google_contact_number": place.get("nationalPhoneNumber", ""),
        "google_rating": round(_safe_float(rating), 1) if rating is not None else None,
        "google_reviews": reviews,
        "google_business_type": ", ".join(place.get("types", [])).replace("_", " "),
        "google_business_status": place.get("businessStatus", ""),
        "google_place_id": place_id or place.get("name", ""),
        "google_location": google_location,
        "google_name_match_score": round(match_score * 100, 1),
        "google_distance_km": round(distance, 2),
        "google_notes": notes,
        "google_pinlocation": (
            f"https://www.google.com/maps?q={place_location[0]},{place_location[1]}"
            if place_location else ""
        ),
        "google_score": google_sort_score(rating, reviews),
        "city": matched_city,
        "state": matched_state,
        "pincode": pincode,
    }
    return google_data, "; ".join(failure_reasons)


def record_with_google_data(
    record: DealerRecord,
    google_data: dict | None,
    *,
    verified: bool | None = None,
    reason: str = "",
) -> DealerRecord:
    if not google_data:
        return replace(
            record,
            google_verified=False if verified is None else verified,
            google_verification_status="Unverified",
            google_verification_reason=reason,
        )
    allowed_fields = {field.name for field in fields(record)}
    filtered_data = {
        key: value
        for key, value in google_data.items()
        if key in allowed_fields
    }
    is_verified = is_operational_with_google_data(filtered_data) if verified is None else verified
    return replace(
        record,
        google_verified=is_verified,
        google_verification_status="Verified" if is_verified else "Unverified",
        google_verification_reason="" if is_verified else reason,
        **filtered_data,
    )


def is_operational_with_google_data(google_data: dict) -> bool:
    return str(google_data.get("google_business_status") or "").casefold() == "operational"


def sort_google_verified(records: Iterable[DealerRecord]) -> list[DealerRecord]:
    return sorted(
        records,
        key=lambda record: (
            _safe_float(record.google_rating) >= PREFERRED_RATING
            and _safe_int(record.google_reviews) >= PREFERRED_REVIEWS,
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
    include_unverified: bool = False,
) -> list[DealerRecord]:
    """Verify records, filter non-operational Google matches, and sort."""
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
    unverified = []
    for index, record in enumerate(record_list, start=1):
        if on_progress:
            on_progress(index, len(record_list), record)
        try:
            google_data, reason = fetch_google_data(
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
            reason = f"Google API error: {exc}"
        is_verified = bool(google_data and is_operational_with_google_data(google_data))
        enriched = record_with_google_data(
            record,
            google_data,
            verified=is_verified,
            reason=reason or "Did not pass Google verification",
        )
        if is_verified:
            verified.append(enriched)
        elif include_unverified:
            unverified.append(enriched)

    sorted_verified = sort_google_verified(verified)
    return sorted_verified + unverified if include_unverified else sorted_verified


def _same_verified_dealer(left: DealerRecord, right: DealerRecord) -> bool:
    left_name = left.google_name or left.name
    right_name = right.google_name or right.name
    if company_name_score(left_name, right_name) < MIN_NAME_MATCH_SCORE:
        return False
    left_address = _clean_text(left.google_full_address or "")
    right_address = _clean_text(right.google_full_address or "")
    if not left_address or not right_address:
        return False
    address_score = SequenceMatcher(None, left_address, right_address).ratio()
    if address_score >= 0.78:
        return True
    left_pin, right_pin = _pincode(left_address), _pincode(right_address)
    overlap = _address_tokens(left_address) & _address_tokens(right_address)
    return bool(left_pin and left_pin == right_pin and len(overlap) >= 2)


def merge_verified_multibrand(records: Iterable[DealerRecord]) -> list[DealerRecord]:
    """Merge post-verification duplicates into one multi-brand dealer."""
    merged: list[DealerRecord] = []
    for record in records:
        match_index = next(
            (
                index for index, existing in enumerate(merged)
                if _same_verified_dealer(existing, record)
            ),
            None,
        )
        if match_index is None:
            merged.append(record)
            continue
        existing = merged[match_index]

        def joined_values(*values: str) -> str:
            unique = []
            for value in values:
                for item in str(value or "").split(","):
                    item = item.strip()
                    if item and item.casefold() not in {
                        current.casefold() for current in unique
                    }:
                        unique.append(item)
            return ", ".join(unique)

        brands = joined_values(existing.source_brand, record.source_brand)
        notes = joined_values(
            existing.google_notes,
            record.google_notes,
            f"multi-brand: {brands}",
        )
        merged[match_index] = replace(
            existing,
            source_brand=brands,
            category=joined_values(existing.category, record.category),
            phone=joined_values(existing.phone, record.phone),
            google_notes=notes,
        )
    return merged


def verify_brand_records_with_google_v2(
    records: Iterable[DealerRecord],
    *,
    api_key: str | None = None,
    city: str = "",
    state: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    on_progress: Callable[[int, int, DealerRecord], None] | None = None,
    include_unverified: bool = True,
) -> list[DealerRecord]:
    """Run anchored, independent-signal verification for brand dealers."""
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
    verified, unverified = [], []
    for index, record in enumerate(record_list, start=1):
        if on_progress:
            on_progress(index, len(record_list), record)
        try:
            google_data, reason = fetch_brand_google_data_v2(
                record,
                api_key=resolved_api_key,
                city=city,
                state=state,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"[Google Places V2] {record.name}: {exc}")
            google_data, reason = None, f"Google API error: {exc}"
        enriched = record_with_google_data(
            record,
            google_data,
            verified=bool(google_data and not reason),
            reason=reason or "Did not pass Google verification",
        )
        if enriched.google_verified:
            verified.append(enriched)
        elif include_unverified:
            unverified.append(enriched)

    return merge_verified_multibrand(verified) + unverified
