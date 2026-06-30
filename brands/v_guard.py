"""V-Guard dealer locator scraper."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class VGuardHandler(BaseBrandHandler):
    BRAND_NAME = "V-Guard"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans", "solar water heaters"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://www.vguard.in/home/dealer-service-network"
    PINCODE_RE = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")
    PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")
    EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        district = self._normalize(city)
        if not state or not district:
            raise ValueError("State and district/city are required for V-Guard.")

        failures = []
        for headless in (True,):
            mode = "headless" if headless else "visible"
            try:
                records = self._scrape_browser(
                    category, state, district, headless=headless
                )
                if records:
                    return records
                failures.append(f"{mode}: 0 rendered dealer cards")
                print(f"[V-Guard] {failures[-1]}")
            except Exception as exc:
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[V-Guard] {failures[-1]}")

        raise RuntimeError(
            "V-Guard browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/v_guard_error.png and output/v_guard_error.html"
        )

    def _scrape_browser(
        self,
        category: str,
        state: str,
        district: str,
        *,
        headless: bool,
    ) -> List[DealerRecord]:
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={self.session_headers['User-Agent']}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        driver = None
        stage = "starting Chrome"
        try:
            print(f"[V-Guard] Opening locator for {district}, {state}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading V-Guard locator"
            driver.get(self.LOCATOR_URL)
            time.sleep(3)
            self._close_popups(driver)
            driver.execute_script("document.body.style.display = 'block';")

            stage = "selecting Dealer"
            location_select = wait.until(
                lambda browser: browser.find_element(By.ID, "location_id")
            )
            self._select_by_value_or_text(driver, location_select, "dealer_locations")

            stage = f"selecting state '{state}'"
            state_select = wait.until(lambda browser: self._dealer_state_select(browser))
            self._select_by_value_or_text(driver, state_select, state)

            stage = f"waiting for district options for '{state}'"
            district_select = wait.until(
                lambda browser: self._district_select_with_options(browser)
            )

            stage = f"selecting district '{district}'"
            self._select_by_value_or_text(driver, district_select, district)
            driver.execute_script(
                "if (typeof showLocations === 'function') { showLocations(1); }"
            )

            stage = "waiting for V-Guard dealer cards"
            wait.until(lambda browser: self._locations_ready(browser))
            time.sleep(1)

            stage = "collecting V-Guard result pages"
            records = []
            seen = set()
            seen_pages = set()
            page = 1
            while True:
                soup = BeautifulSoup(driver.page_source, "html.parser")
                page_records = self._parse_locations(soup, category, state, district)
                page_signature = tuple(self._locations_snapshot(driver)[:6])
                if not page_signature or page_signature in seen_pages:
                    break
                seen_pages.add(page_signature)

                added = 0
                for record in page_records:
                    key = (
                        record.name.casefold(),
                        record.address.casefold(),
                        record.phone,
                    )
                    if key not in seen:
                        seen.add(key)
                        records.append(record)
                        added += 1
                print(
                    f"[V-Guard] Page {page}: {added} new records "
                    f"({len(records)} total)"
                )

                next_page = self._next_page_number(driver)
                if next_page is None:
                    break
                try:
                    old_signature = self._live_signature(driver)
                    driver.execute_script("showLocations(arguments[0]);", next_page)
                    wait.until(
                        lambda browser: self._live_signature(browser) != old_signature
                    )
                    wait.until(lambda browser: self._locations_ready(browser))
                    time.sleep(1)
                    page = next_page
                except Exception as page_exc:
                    print(
                        "[V-Guard] Stopping pagination after rendered results: "
                        f"{type(page_exc).__name__}: {str(page_exc).splitlines()[0]}"
                    )
                    break

            print(f"[V-Guard] Parsed {len(records)} dealer cards")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _parse_locations(
        self,
        soup,
        category: str,
        state: str,
        district: str,
    ) -> List[DealerRecord]:
        records = []
        for cell in soup.select("#locations table td[valign='top']"):
            record = self._parse_card(cell, category, state, district)
            if record is not None:
                records.append(record)
        return records

    def _parse_card(self, cell, category: str, state: str, district: str):
        name_node = cell.select_one("div[style*='f39200']")
        if name_node is None:
            return None
        name = name_node.get_text(" ", strip=True)

        text = cell.get_text("\n", strip=True)
        if self._norm(district) not in self._norm(text):
            return None

        products = self._field(text, r"Products:\s*([^\n]+)")
        if self._is_fans_category(category) and "fan" not in products.casefold():
            return None
        if self._is_solar_water_heater_category(category) and "solar water heater" not in products.casefold():
            return None

        phone_link = cell.select_one("a[href^='tel:']")
        email_link = cell.select_one("a[href^='mailto:']")
        map_link = cell.select_one("a[href*='google.com/maps'], a[href*='maps?q=']")

        phone = (
            phone_link.get("href", "").removeprefix("tel:")
            if phone_link
            else self._field(text, r"Ph:\s*([^\n]+)") or self._first_match(self.PHONE_RE, text)
        )
        email = (
            email_link.get("href", "").removeprefix("mailto:")
            if email_link
            else self._field(text, r"Email:\s*([^\n]+)") or self._first_match(self.EMAIL_RE, text)
        )
        pincode = self._field(text, r"Pincode:\s*(\d{6})") or self._first_match(
            self.PINCODE_RE, text
        )
        latitude, longitude = self._lat_long_from_map(
            map_link.get("href", "") if map_link else ""
        )

        address_parts = []
        for element in name_node.next_siblings:
            if getattr(element, "name", None) == "b":
                break
            part = element.get_text(" ", strip=True) if hasattr(element, "get_text") else str(element)
            part = part.strip()
            if part:
                address_parts.append(part)
        address = " ".join(address_parts)

        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name),
            phone=self._normalize(phone),
            email=self._normalize(email),
            address=self._normalize(address),
            city=district,
            state=state,
            pincode=pincode,
            dealer_type="V-Guard Dealer",
            map_url=map_link.get("href") if map_link else None,
            latitude=latitude,
            longitude=longitude,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _dealer_state_select(driver):
        from selenium.webdriver.common.by import By

        for selector in ("#dealer_drop select.stateDrop", "select.stateDrop"):
            for select in driver.find_elements(By.CSS_SELECTOR, selector):
                if select.is_displayed() and select.is_enabled():
                    return select
        return False

    @staticmethod
    def _district_select_with_options(driver):
        from selenium.webdriver.common.by import By

        select = driver.find_element(By.ID, "locationDrop")
        options = [
            option
            for option in select.find_elements(By.TAG_NAME, "option")
            if (option.text or "").strip()
            and "select district" not in (option.text or "").strip().casefold()
        ]
        return select if select.is_displayed() and options else False

    @staticmethod
    def _select_by_value_or_text(driver, select_element, requested: str) -> None:
        from selenium.webdriver.common.by import By

        options = select_element.find_elements(By.TAG_NAME, "option")
        requested_norm = VGuardHandler._norm(requested)
        matching = None
        for option in options:
            value = (option.get_attribute("value") or "").strip()
            label = (option.text or option.get_attribute("textContent") or "").strip()
            value_norm = VGuardHandler._norm(value)
            label_norm = VGuardHandler._norm(label)
            if VGuardHandler._option_matches(requested_norm, value_norm, label_norm):
                matching = option
                break
        if matching is None:
            for option in options:
                value = (option.get_attribute("value") or "").strip()
                label = (option.text or option.get_attribute("textContent") or "").strip()
                value_norm = VGuardHandler._norm(value)
                label_norm = VGuardHandler._norm(label)
                if VGuardHandler._option_matches(requested_norm, value_norm, label_norm):
                    matching = option
                    break
        if matching is None:
            labels = [
                (option.text or option.get_attribute("textContent") or "").strip()
                for option in options
            ]
            raise ValueError(f"Could not find dropdown option {requested!r}. Available: {labels[:20]}")

        value = matching.get_attribute("value")
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            select_element,
            value,
        )

    @classmethod
    def _location_cells(cls, driver):
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import StaleElementReferenceException

        try:
            return [
                cell
                for cell in driver.find_elements(
                    By.CSS_SELECTOR, "#locations table td[valign='top']"
                )
                if cell.is_displayed() and cell.text.strip()
            ]
        except StaleElementReferenceException:
            return []

    @classmethod
    def _locations_ready(cls, driver):
        snapshot = cls._locations_snapshot(driver)
        return snapshot if snapshot else False

    @classmethod
    def _live_signature(cls, driver):
        return tuple(cls._locations_snapshot(driver)[:6])

    @staticmethod
    def _locations_snapshot(driver) -> list[str]:
        try:
            return driver.execute_script(
                """
                const root = document.querySelector('#locations');
                if (!root) return [];
                return [...root.querySelectorAll("table td[valign='top']")]
                  .map((cell) => (cell.innerText || cell.textContent || '').trim())
                  .filter(Boolean);
                """
            ) or []
        except Exception:
            return []

    @staticmethod
    def _next_page_number(driver) -> int | None:
        try:
            href = driver.execute_script(
                """
                const links = [...document.querySelectorAll('#locations .pagination1 a')];
                const next = links.find((link) =>
                  (link.innerText || '').trim().toLowerCase() === 'next' &&
                  window.getComputedStyle(link).display !== 'none'
                );
                return next ? next.getAttribute('href') : '';
                """
            ) or ""
        except Exception:
            href = ""
        match = re.search(r"showLocations\((\d+)\)", href)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _close_popups(driver) -> None:
        from selenium.webdriver.common.by import By

        selectors = (
            "#close_button",
            ".closed",
            "button[aria-label='Close']",
            ".modal .close",
            ".popup-close",
        )
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    driver.execute_script("arguments[0].click();", element)
                    time.sleep(0.5)
        driver.execute_script(
            "const msg=document.getElementById('homepage_message');"
            "if (msg) msg.style.display='none';"
        )

    @staticmethod
    def _is_fans_category(category: str) -> bool:
        return "fan" in str(category or "").casefold()

    @staticmethod
    def _is_solar_water_heater_category(category: str) -> bool:
        text = str(category or "").casefold()
        return "solar" in text and "water heater" in text

    @staticmethod
    def _lat_long_from_map(url: str) -> tuple[str, str]:
        match = re.search(r"q=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", url or "")
        return (match.group(1), match.group(2)) if match else ("", "")

    @staticmethod
    def _field(text: str, pattern: str) -> str:
        match = re.search(pattern, text or "", re.I)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _first_match(pattern, text: str) -> str:
        match = pattern.search(text or "")
        return match.group(1) if match and match.groups() else match.group(0) if match else ""

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @staticmethod
    def _option_matches(requested_norm: str, value_norm: str, label_norm: str) -> bool:
        if not requested_norm:
            return False
        label_initials = "".join(part[:1] for part in label_norm.split())
        alias_map = {
            "ap": "andhra pradesh",
            "ar": "arunachal pradesh",
            "as": "assam",
            "br": "bihar",
            "cg": "chhattisgarh",
            "ch": "chandigarh",
            "dl": "delhi",
            "ga": "goa",
            "gj": "gujarat",
            "hr": "haryana",
            "hp": "himachal pradesh",
            "jh": "jharkhand",
            "jk": "jammu and kashmir",
            "ka": "karnataka",
            "kl": "kerala",
            "mh": "maharashtra",
            "mp": "madhya pradesh",
            "od": "odisha",
            "or": "odisha",
            "pb": "punjab",
            "rj": "rajasthan",
            "tn": "tamil nadu",
            "ts": "telangana",
            "up": "uttar pradesh",
            "uk": "uttarakhand",
            "wb": "west bengal",
        }
        requested_alias = alias_map.get(requested_norm, requested_norm)
        return (
            requested_norm in {value_norm, label_norm, label_initials}
            or requested_alias == label_norm
            or bool(label_norm and requested_norm in label_norm)
            or bool(label_norm and label_norm in requested_norm)
            or bool(value_norm and requested_norm in value_norm)
            or bool(value_norm and value_norm in requested_norm)
        )

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "v_guard_error.png"))
            (output / "v_guard_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
