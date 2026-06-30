"""Orient Electric Fans dealer locator scraped from its rendered page."""

import re
import time
from pathlib import Path
from typing import List

import requests

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class OrientHandler(BaseBrandHandler):
    BRAND_NAME = "Orient"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://orientelectric.com/pages/store-locator"
    SHEET_URL_ENDPOINT = "https://brand.orientelectric.com/api/getSheetUrl"
    PRODUCT_CATEGORY = "Fans"
    CITY_ALIASES = {"bengaluru": "BANGALORE", "bangalore": "BANGALORE"}
    CONTROL_WAIT_TIMEOUT = 15

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not state or not city:
            raise ValueError("State and city are required for Orient.")
        locator_city = self.CITY_ALIASES.get(city.casefold(), city)

        try:
            records = self._fetch_sheet_data(category, state, locator_city)
            print(f"[Orient] Parsed {len(records)} dealer records from locator data")
            return records
        except Exception as exc:
            print(f"[Orient] Locator data fallback failed: {type(exc).__name__}: {exc}")

        failures = []
        for headless in (True,):
            try:
                return self._scrape_browser(
                    category, state, locator_city, headless=headless
                )
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Orient] {failures[-1]}")
                if self._is_page_load_timeout(exc):
                    break

        raise RuntimeError(
            "Orient browser scraper failed after headless and visible attempts. "
            + " | ".join(failures)
            + " Diagnostic files: output/orient_error.png and output/orient_error.html"
        )

    def _fetch_sheet_data(
        self, category: str, state: str, city: str
    ) -> List[DealerRecord]:
        response = requests.get(
            self.SHEET_URL_ENDPOINT,
            headers=self.session_headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        sheet_url = response.json().get("data")
        if not sheet_url:
            raise RuntimeError("Orient sheet URL was not returned.")

        sheet_response = requests.get(
            sheet_url,
            headers=self.session_headers,
            timeout=max(self.timeout, 30),
        )
        sheet_response.raise_for_status()
        rows = sheet_response.json().get("values") or []
        if len(rows) < 2:
            return []

        records = []
        state_norm = self._norm(state)
        city_norm = self._norm(city)
        for row in rows[1:]:
            row = list(row) + [""] * 11
            row_category = row[1]
            row_name = row[2]
            row_state = row[3]
            row_city = row[4]
            row_address = row[8]
            row_phone = row[10]

            if self._norm(row_category) != self._norm(self.PRODUCT_CATEGORY):
                continue
            if self._norm(row_state) != state_norm:
                continue
            if self._norm(row_city) != city_norm:
                continue

            pincode_match = re.search(r"\b\d{6}\b", row_address or "")
            record = self._make_record(
                category=category,
                state_name=state,
                name=self._normalize(row_name),
                phone=self._normalize(row_phone),
                address=self._normalize(row_address),
                city=self._normalize(row_city),
                state=self._normalize(row_state),
                pincode=pincode_match.group(0) if pincode_match else "",
                dealer_type="Authorized Dealer",
            )
            if record.is_valid():
                records.append(record)
        return records

    def _scrape_browser(
        self, category: str, state: str, city: str, *, headless: bool
    ) -> List[DealerRecord]:
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={self.session_headers['User-Agent']}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option(
            "prefs",
            {"profile.managed_default_content_settings.images": 2},
        )
        options.page_load_strategy = "none"

        driver = None
        stage = "starting Chrome"
        try:
            mode = "headless" if headless else "visible"
            print(f"[Orient] Opening {mode} locator for {city}, {state}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, min(max(self.timeout, 15), 22))
            stage = "loading the Orient locator"
            driver.get(self.LOCATOR_URL)
            WebDriverWait(driver, self.CONTROL_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "new-slcatid"))
            )
            driver.execute_script("window.stop();")
            time.sleep(1)
            self._dismiss_consent(driver)

            stage = "selecting Fans product category"
            self._select_value(
                driver, wait, "new-slcatid", self.PRODUCT_CATEGORY
            )

            stage = f"selecting state '{state}'"
            self._select_value(driver, wait, "new-slstate", state)

            stage = f"selecting city '{city}'"
            self._select_value(driver, wait, "new-slcity", city)

            stage = "submitting the Orient dealer search"
            submit = wait.until(
                EC.presence_of_element_located((By.ID, "goSubmitButton"))
            )
            driver.execute_script("arguments[0].click();", submit)

            stage = "waiting for Orient dealer cards"
            wait.until(
                lambda browser: self._has_results(browser)
                or self._no_results(browser)
            )
            time.sleep(0.5)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select(
                ".store-location-details-new:not(.hidden) "
                ".address-list--inner"
            )
            records = [
                record
                for card in cards
                if (record := self._parse_card(card, category, state, city))
                is not None
            ]
            print(f"[Orient] Parsed {len(records)} dealer cards")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    @staticmethod
    def _dismiss_consent(driver) -> None:
        from selenium.webdriver.common.by import By

        selectors = (
            "#cookieNotice .cookie-btn",
            ".cookieNotice-wrapper .cookie-btn",
            ".cookieNotice-wrapper .closeBtn",
        )
        for selector in selectors:
            for control in driver.find_elements(By.CSS_SELECTOR, selector):
                if control.is_displayed():
                    driver.execute_script("arguments[0].click();", control)
                    time.sleep(0.3)
                    return

    @staticmethod
    def _select_value(driver, wait, select_id: str, requested: str) -> None:
        from selenium.webdriver.common.by import By

        select = wait.until(
            lambda browser: browser.find_element(By.ID, select_id)
        )

        def matching_option(browser):
            for option in browser.find_elements(
                By.CSS_SELECTOR, f"#{select_id} option"
            ):
                value = (option.get_attribute("value") or "").strip()
                label = (option.get_attribute("textContent") or "").strip()
                requested_norm = OrientHandler._norm(requested)
                value_norm = OrientHandler._norm(value)
                label_norm = OrientHandler._norm(label)
                if OrientHandler._option_matches(
                    requested_norm, value_norm, label_norm
                ):
                    return option
            return False

        option = wait.until(matching_option)
        value = option.get_attribute("value")
        driver.execute_script(
            "arguments[0].value=arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
            select,
            value,
        )

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @staticmethod
    def _option_matches(requested_norm: str, value_norm: str, label_norm: str) -> bool:
        if not requested_norm:
            return False
        alias_map = {
            "ka": "karnataka",
            "mp": "madhya pradesh",
            "mh": "maharashtra",
            "tn": "tamil nadu",
            "up": "uttar pradesh",
            "wb": "west bengal",
        }
        requested_alias = alias_map.get(requested_norm, requested_norm)
        return (
            requested_norm in {value_norm, label_norm}
            or requested_alias == label_norm
            or bool(label_norm and requested_norm in label_norm)
            or bool(label_norm and label_norm in requested_norm)
            or bool(value_norm and requested_norm in value_norm)
            or bool(value_norm and value_norm in requested_norm)
        )

    @staticmethod
    def _is_page_load_timeout(exc: Exception) -> bool:
        text = str(exc).casefold()
        return "err_connection_timed_out" in text or "timed out receiving message" in text

    @staticmethod
    def _has_results(driver) -> bool:
        from selenium.webdriver.common.by import By

        cards = driver.find_elements(
            By.CSS_SELECTOR,
            ".store-location-details-new:not(.hidden) .address-list--inner",
        )
        return any(
            card.find_elements(By.CSS_SELECTOR, ".store-name-new")
            and card.text.strip()
            for card in cards
        )

    @staticmethod
    def _no_results(driver) -> bool:
        from selenium.webdriver.common.by import By

        elements = driver.find_elements(By.CSS_SELECTOR, "#no-found:not(.hidden)")
        return any(element.is_displayed() for element in elements)

    def _parse_card(self, card, category: str, state: str, city: str):
        name = card.select_one(".store-name-new, .store-name")
        address = card.select_one(".store_address, .store-location")
        phone_link = card.select_one("a[href^='tel:']")
        phone_text = card.select_one(".store_phone span, .store_phone")
        if name is None or not name.get_text(" ", strip=True):
            return None

        address_text = address.get_text(" ", strip=True) if address else ""
        phone = ""
        if phone_link:
            phone = phone_link.get("href", "").removeprefix("tel:")
        elif phone_text:
            phone = phone_text.get_text(" ", strip=True)
        pincode_match = re.search(r"\b\d{6}\b", address_text)
        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name.get_text(" ", strip=True)),
            phone=self._normalize(phone),
            address=self._normalize(address_text),
            city=city,
            state=state,
            pincode=pincode_match.group(0) if pincode_match else "",
            dealer_type="Authorized Dealer",
        )
        return record if record.is_valid() else None

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "orient_error.png"))
            (output / "orient_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
