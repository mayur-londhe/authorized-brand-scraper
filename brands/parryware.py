"""
Parryware – Showroom Locator Plugin
===================================
Uses the Liferay PlacesMap portlet XHR endpoint on parryware.in.

Search is city + state based (e.g. "Bengaluru, Karnataka, India").
Coordinates come from OpenStreetMap Nominatim geocoding.
"""

import re
import time
from typing import List, Tuple
from urllib.parse import urlencode

import requests

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord

PAGE_URL = "https://www.parryware.in/where-to-buy-showrooms"
INSTANCE_RE = re.compile(r"PlacesMap_INSTANCE_[a-zA-Z0-9]+")
DEFAULT_DISTANCE_KM = 10
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

CATEGORY_MAP = {
    "water efficient fixtures": "",
    "showrooms": "",
    "bathroom fixtures": "",
    "fixtures": "",
}


class ParrywareHandler(BaseBrandHandler):
    BRAND_NAME = "Parryware"
    SUPPORTED_CATEGORIES = list(CATEGORY_MAP.keys())
    REQUIRES_CITY = True

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        city = self._normalize(city)
        state = self._normalize(state)

        if not city:
            print("[Parryware] --city is required (e.g. Bengaluru, Mumbai, Indore).")
            return []

        if not state:
            print("[Parryware] --state is required (e.g. Karnataka, Maharashtra).")
            return []

        api_category = CATEGORY_MAP.get(category.lower(), "")

        try:
            print("\n[Parryware] ================================")
            print("[Parryware] Source: API")
            print(f"[Parryware] City: {city}")
            print(f"[Parryware] State: {state}")
            print(f"[Parryware] Category: {category}")
            print("[Parryware] Initializing API flow...")

            session, instance_id = self._warm_session()

            print(f"[Parryware] Portlet instance found: {instance_id}")

            lat, lon = self._geocode(city, state)

            print(f"[Parryware] Geocoded location: lat={lat}, lon={lon}")

            search = f"{city}, {state}, India"

            print(f"[Parryware] Fetching dealer data via API for: {search}")

            data = self._call_api(
                session=session,
                instance_id=instance_id,
                search=search,
                lat=lat,
                lon=lon,
                cat_filter=api_category,
            )

            records = self._parse_api_response(data, category, state)

            print(
                f"[Parryware] ✅ Results source: API | "
                f"Dealers found: {len(records)}"
            )
            print("[Parryware] ================================\n")

            return records

        except Exception as e:
            print(f"[Parryware] ❌ API error: {e}")

            # Placeholder for future scraping fallback
            print("[Parryware] ⚠️ No scraping fallback configured.")

            return []

    def _warm_session(self) -> Tuple[requests.Session, str]:
        """Load the locator page (cache-busted) to obtain JSESSIONID and portlet id."""
        print("[Parryware] Warming session...")

        session = requests.Session()
        session.headers.update(
            {
                **self.session_headers,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )

        resp = session.get(
            PAGE_URL,
            params={"_": int(time.time() * 1000)},
            timeout=self.timeout,
        )
        resp.raise_for_status()

        print(f"[Parryware] Page loaded successfully ({resp.status_code})")

        match = INSTANCE_RE.search(resp.text)
        if not match:
            raise RuntimeError("PlacesMap portlet instance id not found on page")

        if not session.cookies.get("JSESSIONID"):
            raise RuntimeError(
                "Could not establish Parryware session (missing JSESSIONID)"
            )

        print("[Parryware] Session established successfully")

        return session, match.group(0)

    def _geocode(self, city: str, state: str) -> Tuple[float, float]:
        query = f"{city}, {state}, India"

        print(f"[Parryware] Geocoding: {query}")

        resp = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "json",
                "limit": 1,
            },
            headers={
                "User-Agent": (
                    "dealer-scraper/1.0 "
                    "(Parryware showroom locator)"
                )
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()

        results = resp.json()

        if not results:
            raise RuntimeError(
                f"Could not geocode location: {query!r}"
            )

        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])

        return lat, lon

    def _call_api(
        self,
        session: requests.Session,
        instance_id: str,
        search: str,
        lat: float,
        lon: float,
        cat_filter: str,
        distance_km: int = DEFAULT_DISTANCE_KM,
    ) -> dict:
        prefix = f"_{instance_id}"

        payload = {
            f"{prefix}_action": "sellingPointsList",
            f"{prefix}_catFilter": cat_filter or "",
            f"{prefix}_locFilter": "",
            f"{prefix}_search": search,
            f"{prefix}_userLatitude": str(lat),
            f"{prefix}_userLongitude": str(lon),
            f"{prefix}_distance": str(distance_km),
        }

        api_url = (
            f"{PAGE_URL}?p_p_id={instance_id}"
            "&p_p_lifecycle=2"
            "&p_p_state=normal"
            "&p_p_mode=view"
            "&p_p_cacheability=cacheLevelPage"
        )

        print("[Parryware] Calling API endpoint...")
        print(f"[Parryware] Search query: {search}")
        print(f"[Parryware] Distance: {distance_km} km")

        resp = session.post(
            api_url,
            data=urlencode(payload),
            headers={
                "Accept": "*/*",
                "Content-Type": (
                    "application/x-www-form-urlencoded; "
                    "charset=UTF-8"
                ),
                "Origin": "https://www.parryware.in",
                "Referer": PAGE_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.timeout,
        )

        resp.raise_for_status()

        print(
            f"[Parryware] API request successful "
            f"({resp.status_code})"
        )

        data = resp.json()

        if not data.get("success"):
            message = data.get("message") or "unknown error"
            raise RuntimeError(
                f"Parryware API returned success=false: {message}"
            )

        locations = data.get("locations") or []

        if locations:
            print(
                "[Parryware] API response successful | "
                f"Locations returned: {len(locations)}"
            )
        else:
            print(
                "[Parryware] API response successful "
                "but no locations found"
            )

        return data

    def _parse_api_response(
        self,
        data: dict,
        category: str,
        state: str,
    ) -> List[DealerRecord]:
        print("[Parryware] Parsing API response...")

        records: List[DealerRecord] = []

        for item in data.get("locations") or []:
            phones = item.get("phones") or []
            emails = item.get("emails") or []
            categories = item.get("cats") or []

            dealer_type = ""
            if categories:
                dealer_type = self._normalize(
                    categories[0].get("catName", "")
                )

            record = self._make_record(
                category=category,
                state_name=state,
                name=self._normalize(item.get("name", "")),
                phone=self._normalize(
                    phones[0] if phones else ""
                ),
                email=self._normalize(
                    emails[0] if emails else ""
                ),
                address=self._normalize(
                    item.get("address", "")
                ),
                city=self._normalize(item.get("city", "")),
                state=state,
                pincode=self._normalize(
                    str(item.get("zipcode", "") or "")
                ),
                dealer_type=dealer_type or "Showroom",
                latitude=str(item.get("lat", "") or ""),
                longitude=str(item.get("lon", "") or ""),
            )

            if record.is_valid():
                records.append(record)

        print(
            f"[Parryware] Parsed {len(records)} "
            "valid dealer records"
        )

        return records
