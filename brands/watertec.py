"""Watertec dealer network plugin using the endpoints exposed by its locator page."""

import json
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class WatertecHandler(BaseBrandHandler):
    BRAND_NAME = "Watertec"
    SUPPORTED_CATEGORIES = ["water efficient fixtures", "bathware", "plumbing"]
    REQUIRES_CITY = True

    BASE_URL = "https://watertecindia.com"
    LOCATOR_URL = f"{BASE_URL}/dealer-network"

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not state or not city:
            raise ValueError("State and city are required for Watertec.")

        session = requests.Session()
        session.headers.update({**self.session_headers, "Referer": self.LOCATOR_URL})
        page = session.get(self.LOCATOR_URL, timeout=self.timeout)
        page.raise_for_status()

        state_id, city_id = self._resolve_location(page.text, state, city)
        base_params = {
            "country": "India",
            "state": state_id,
            "city": city_id,
        }
        count_response = session.get(
            f"{self.BASE_URL}/api/dealers/managecount",
            params=base_params,
            timeout=self.timeout,
        )
        count_response.raise_for_status()
        total = self._dealer_count(count_response.json())

        dealers = []
        page_size = 100
        page_number = 1
        while total is None or len(dealers) < total:
            response = session.get(
                f"{self.BASE_URL}/api/dealers/manage",
                params={
                    **base_params,
                    "pageNumber": page_number,
                    "pageSize": page_size,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            page = self._dealer_list(response.json())
            dealers.extend(page)
            if not page or len(page) < page_size or total is None:
                break
            page_number += 1
        print(f"[Watertec] Found {len(dealers)} dealers for {city}, {state}")
        return self._parse_dealers(dealers, category, state, city)

    @staticmethod
    def _resolve_location(html: str, state: str, city: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        state_option = next(
            (
                option
                for option in soup.select("#statename option[value]")
                if option.get_text(" ", strip=True).casefold() == state.casefold()
            ),
            None,
        )
        if state_option is None:
            raise ValueError(f"Watertec state not found: {state}")
        state_id = state_option.get("value", "")

        match = re.search(r"let\s+cities_data\s*=\s*(\[.*?\]);", html, re.DOTALL)
        if match is None:
            raise RuntimeError("Watertec city data was not found in the locator page.")
        cities = json.loads(match.group(1))
        city_row = next(
            (
                row
                for row in cities
                if str(row.get("state", "")) == state_id
                and str(row.get("title", "")).strip().casefold() == city.casefold()
            ),
            None,
        )
        if city_row is None:
            raise ValueError(f"Watertec city not found in {state}: {city}")
        return state_id, str(city_row.get("_id", ""))

    @staticmethod
    def _dealer_list(payload) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        data = payload.get("dealer_data", payload.get("data", []))
        if isinstance(data, dict):
            data = data.get("dealer_data", data.get("data", []))
        return data if isinstance(data, list) else []

    @staticmethod
    def _dealer_count(payload) -> int | None:
        if isinstance(payload, int):
            return payload
        if isinstance(payload, dict):
            value = payload.get("dealer_data", payload.get("count"))
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _parse_dealers(
        self, dealers: list[dict], category: str, state: str, city: str
    ) -> List[DealerRecord]:
        records = []
        for item in dealers:
            item_state = item.get("state") or {}
            item_city = item.get("city") or {}
            record = self._make_record(
                category=category,
                state_name=state,
                name=self._normalize(str(item.get("name", ""))),
                phone=self._normalize(str(item.get("mobile", "") or "")),
                email=self._normalize(str(item.get("email", "") or "")),
                address=self._normalize(str(item.get("address", "") or "")),
                city=self._nested_title(item_city) or city,
                state=self._nested_title(item_state) or state,
                dealer_type="Authorized Dealer",
                latitude=self._normalize(str(item.get("latitude", "") or "")),
                longitude=self._normalize(str(item.get("longitude", "") or "")),
            )
            if record.is_valid():
                records.append(record)
        return records

    @staticmethod
    def _nested_title(value) -> str:
        if isinstance(value, dict):
            return str(value.get("title", "") or "").strip()
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return str(value[0].get("title", "") or "").strip()
        return ""
