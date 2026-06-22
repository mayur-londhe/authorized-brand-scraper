"""GM Modular experience centres, rendered directly on its locator page."""

import re
from typing import List

import requests
from bs4 import BeautifulSoup

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class GMModularHandler(BaseBrandHandler):
    BRAND_NAME = "GM Modular"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://www.gmmodular.com/store-locator"
    INDIAN_STATES = (
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
        "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
        "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
        "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
        "Delhi", "Jammu and Kashmir", "Puducherry",
    )

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        city = self._normalize(city)
        if not city:
            raise ValueError("City is required for GM Modular.")
        response = requests.get(
            self.LOCATOR_URL, headers=self.session_headers, timeout=self.timeout
        )
        response.raise_for_status()
        records = self._parse_html(
            response.text, category, self._normalize(state), city
        )
        print(f"[GM Modular] Parsed {len(records)} stores for {city}")
        return records

    def _parse_html(
        self,
        html: str,
        category: str,
        requested_state: str = "",
        requested_city: str = "",
    ) -> List[DealerRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records = []
        for card in soup.select(".locatorcol"):
            heading = card.select_one("h5")
            address_node = card.select_one("p")
            if heading is None or address_node is None:
                continue

            city = self._normalize(heading.get_text(" ", strip=True))
            address = self._normalize(address_node.get_text(" ", strip=True))
            if requested_city and not self._city_matches(requested_city, city, address):
                continue
            phone_link = card.select_one('a[href^="tel:"]')
            email_link = card.select_one('a[href^="mailto:"]')
            location_link = card.select_one("a.locatebtn")
            pincode = re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", address)
            actual_state = next(
                (name for name in self.INDIAN_STATES if re.search(
                    rf"\b{re.escape(name)}\b", address, re.IGNORECASE
                )),
                requested_state,
            )

            record = self._make_record(
                category=category,
                state_name=requested_state,
                name=f"GM Experience Center - {city}",
                phone=self._node_text(phone_link),
                email=self._node_text(email_link),
                address=address,
                city=city,
                state=actual_state,
                pincode=pincode.group(0) if pincode else "",
                dealer_type="GM Experience Center",
                website=location_link.get("href", "") if location_link else self.LOCATOR_URL,
            )
            if record.is_valid():
                records.append(record)
        return records

    @staticmethod
    def _city_matches(requested_city: str, listed_city: str, address: str) -> bool:
        aliases = {
            "bangalore": {"bangalore", "bengaluru"},
            "bengaluru": {"bangalore", "bengaluru"},
            "belgaum": {"belgaum", "belgavi", "belagavi"},
            "belgavi": {"belgaum", "belgavi", "belagavi"},
        }
        wanted = requested_city.casefold().strip()
        candidates = aliases.get(wanted, {wanted})
        haystack = f"{listed_city} {address}".casefold()
        return any(
            re.search(rf"\b{re.escape(candidate)}\b", haystack)
            for candidate in candidates
        )

    def _node_text(self, node) -> str:
        return self._normalize(node.get_text(" ", strip=True)) if node else ""
