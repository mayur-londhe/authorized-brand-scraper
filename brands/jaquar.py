"""
Jaquar – Dealer Locator Plugin
==============================
Priority: API (XHR GET) → Browser Fallback (Selenium Automation)

Designed for integration with the Multi-Brand Orchestration Engine framework.
"""

import time
import requests
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class JaquarHandler(BaseBrandHandler):
    BRAND_NAME = "Jaquar"
    SUPPORTED_CATEGORIES = ["bath", "light", "faucets", "showers", "sanitaryware"]

    # --- API Target Network Endpoints ---
    BASE_URL = "https://www.jaquar.com"
    STATES_API = f"{BASE_URL}/en/Customer/GetStatesByDealerType"
    CITIES_API = f"{BASE_URL}/en/Customer/CitySearchAutoComplete"
    DEALERS_API = f"{BASE_URL}/en/customer/getdealerlocatorbycountryid"

    # Default UI map definitions matching form layouts
    CATEGORY_MAPPING = {
        "bath": "Bathroom",
        "faucets": "Bathroom",
        "showers": "Bathroom",
        "sanitaryware": "Bathroom",
        "light": "Lighting"
    }

    CITY_ALIASES = {
        "bengaluru": "Bangalore",
    }

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        """
        Main orchestration entry routing rule. Runs clean dynamic payload checks 
        using internal brand maps.
        """
        # Map requesting criteria to dealer layout keys
        dealer_type = self.CATEGORY_MAPPING.get(category.lower(), "Bathroom")
        city = self.CITY_ALIASES.get(city.strip().casefold(), city.strip())

        # Execute direct backend lookup pipeline first
        records = self._try_api(category, dealer_type, state, city)
        if records is not None and len(records) > 0:
            return records

        # Fallback to visual automation frame parsing if API state indexes shift
        return self._scrape_html(category, dealer_type, state, city)

    # ── API Integration Path ──────────────────────────────────────────
    def _try_api(self, category: str, dealer_type: str, state_query: str, city_query: str) -> List[DealerRecord] | None:
        """
        Queries Jaquar's dynamic API routing structure by resolving state IDs and city tokens.
        """
        print(f"[{self.BRAND_NAME}] Console: Fetching dealer data records directly via JSON API endpoint...")
        
        headers = {
            **self.session_headers,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE_URL}/en/find-dealers"
        }

        try:
            # 1. Resolve State Text string into Jaquar's structural internal State ID
            state_id = None
            state_params = {"countryId": "237", "dealerType": dealer_type} # 237 maps to India baseline
            
            state_resp = requests.get(self.STATES_API, params=state_params, headers=headers, timeout=self.timeout)
            if state_resp.status_code == 200:
                for state_obj in state_resp.json():
                    if state_query.lower() in state_obj.get("name", "").lower():
                        state_id = str(state_obj.get("id"))
                        break

            # If state string matching yields no explicit code indices, exit to fallback parsing
            if not state_id:
                print(f"[{self.BRAND_NAME}] Warning: Could not resolve State ID for '{state_query}'.")
                return None

            # 2. Match City parameter strings if passed through context
            resolved_city_name = ""
            if city_query:
                city_params = {"prefix": "", "StateId": state_id, "DealerType": dealer_type}
                city_resp = requests.get(self.CITIES_API, params=city_params, headers=headers, timeout=self.timeout)
                if city_resp.status_code == 200:
                    for city_obj in city_resp.json():
                        if city_query.lower() in city_obj.get("name", "").lower():
                            resolved_city_name = city_obj.get("name", "")
                            break

            # 3. Fire primary structural Dealer locator lookup request
            dealer_params = {
                "countryId": "237",
                "stateid": state_id,
                "CityName": resolved_city_name if resolved_city_name else (city_query if city_query else ""),
                "DealerType": dealer_type
            }

            resp = requests.get(self.DEALERS_API, params=dealer_params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            
            data = resp.json()
            if not data or not isinstance(data, list):
                return None

            return self._parse_api_response(data, category, state_query, city_query)

        except Exception as e:
            print(f"[{self.BRAND_NAME}] API Pathway encountered an error exception block: {e}")
            return None

    def _parse_api_response(self, data: list, category: str, query_state: str, query_city: str) -> List[DealerRecord]:
        """
        Maps nested dynamic response keys cleanly into standardized framework schema layouts.
        """
        records = []

        for item in data:
            # Map attributes according to internal JSON field variables
            name_text = item.get("Company", "")
            addr_1 = item.get("Address1", "")
            addr_2 = item.get("Address2", "")
            full_address = f"{addr_1} {addr_2}".strip()

            record = self._make_record(
                category=category,
                state_name=query_state,
                name=self._normalize(name_text),
                phone=self._normalize(str(item.get("PhoneNumber", ""))),
                email=self._normalize(str(item.get("Email", ""))),
                address=self._normalize(full_address),
                city=self._normalize(item.get("City", "") or query_city),
                state=self._normalize(item.get("StateProvince", "") or query_state),
                pincode="",  # Parsed downstream inside excel exporter workflows if needed
                dealer_type=self._normalize(item.get("DealerType", "Authorized Dealer")),
                website=self._normalize(item.get("Location", ""))  # Holds explicit Google Maps destination URL strings
            )

            if record.is_valid():
                records.append(record)

        return records

    # ── Browser Fallback Engine Path ─────────────────────────────────────
    def _scrape_html(self, category: str, dealer_type: str, state: str, city: str) -> List[DealerRecord]:
        """
        Processes fallbacks leveraging automated UI element tracking via Selenium drivers.
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from bs4 import BeautifulSoup

            print(f"[{self.BRAND_NAME}] Console: API unavailable. Launching browser automation fallback wrapper...")

            options = webdriver.ChromeOptions()
            options.add_argument("--start-maximized")
            driver = webdriver.Chrome(options=options)
            records = []

            driver.get(f"{self.BASE_URL}/en/find-dealers")
            wait = WebDriverWait(driver, 15)

            # Select target Dealer Category Option type
            if dealer_type != "Bathroom":
                dealer_dropdown = wait.until(EC.presence_of_element_located((By.ID, "DealerType")))
                dealer_dropdown.send_keys(dealer_type)
                time.sleep(1)

            # Interact with State element block nodes
            state_dropdown = wait.until(EC.presence_of_element_located((By.ID, "StateProvinceId")))
            state_dropdown.send_keys(state)
            time.sleep(2)

            # Trigger City option changes if parameters match layout maps
            if city:
                city_dropdown = wait.until(EC.presence_of_element_located((By.ID, "dd_City")))
                city_dropdown.send_keys(city)
                time.sleep(2)

            # Extract rendering markup context straight into BeautifulSoup layout parsers
            soup = BeautifulSoup(driver.page_source, "html.parser")
            driver.quit()

            # Mine elements matching dynamic orientation list class maps
            cards = soup.select(".orient-centre-list") or soup.select(".card-body")
            print(f"[{self.BRAND_NAME}] Console: Safely processing structural card text extractions on {len(cards)} items...")

            for card in cards:
                name_el = card.select_one("h5")
                addr_el = card.select_one("address")
                phone_el = card.select_one(".phone")

                if not name_el:
                    continue

                record = self._make_record(
                    category=category,
                    state_name=state,
                    name=self._normalize(name_el.get_text()),
                    phone=self._normalize(phone_el.get_text().replace("Tel:", "")) if phone_el else "",
                    address=self._normalize(addr_el.get_text().replace("Address:", "")) if addr_el else "",
                    city=self._normalize(city or state),
                    state=self._normalize(state)
                )
                if record.is_valid():
                    records.append(record)

            return records

        except Exception as e:
            print(f"[{self.BRAND_NAME}] Critical structural element parsing exception context: {e}")
            return []
