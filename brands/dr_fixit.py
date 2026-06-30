"""Dr Fixit dealer locator scraped through the rendered browser UI."""

import re
import time
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup
import requests

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class DrFixitHandler(BaseBrandHandler):
    BRAND_NAME = "Dr Fixit"
    SUPPORTED_CATEGORIES = ["cool roof"]
    REQUIRES_CITY = True
    REQUIRES_PINCODE = True

    LOCATOR_URL = "https://www.drfixit.co.in/locate/find-dealer"
    CITY_URL = "https://www.drfixit.co.in/getcities"
    DEALER_URL = "https://www.drfixit.co.in/get-dealer"

    def fetch(
        self, category: str, state: str, city: str = "", pincode: str = ""
    ) -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        pincode = self._normalize(pincode)
        if not state or not city or not pincode:
            raise ValueError("State, city, and pincode are required for Dr Fixit.")

        try:
            records = self._fetch_endpoint(category, state, city, pincode)
            print(f"[Dr Fixit] Parsed {len(records)} dealer records from locator endpoint")
            return records
        except Exception as exc:
            print(f"[Dr Fixit] Locator endpoint fallback failed: {type(exc).__name__}: {exc}")

        failures = []
        for headless in (True,):
            try:
                return self._scrape_browser(category, state, city, pincode, headless=headless)
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Dr Fixit] {failures[-1]}")

        raise RuntimeError(
            "Dr Fixit browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/dr_fixit_error.png and output/dr_fixit_error.html"
        )

    def _fetch_endpoint(
        self, category: str, state: str, city: str, pincode: str
    ) -> List[DealerRecord]:
        session = requests.Session()
        headers = {
            **self.session_headers,
            "Accept": "text/html,application/json,*/*",
            "Referer": self.LOCATOR_URL,
        }
        page = session.get(self.LOCATOR_URL, headers=headers, timeout=self.timeout)
        page.raise_for_status()
        token = self._first_match(r'var token = "([^"]+)"', page.text)
        if not token:
            raise RuntimeError("Could not find Dr Fixit locator token.")

        soup = BeautifulSoup(page.text, "html.parser")
        state_id = self._find_option_value(soup, "state", state)
        if not state_id:
            raise RuntimeError(f"Could not find Dr Fixit state option for {state!r}.")

        ajax_headers = {
            **headers,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        city_response = session.get(
            self.CITY_URL,
            params={"state_id": state_id, "_token": token},
            headers=ajax_headers,
            timeout=self.timeout,
        )
        city_response.raise_for_status()
        city_id = self._find_city_id(city_response.json(), city)
        if not city_id:
            raise RuntimeError(f"Could not find Dr Fixit city option for {city!r}.")

        records = []
        seen_offsets = set()
        offset = "0"
        for _ in range(50):
            if offset in seen_offsets:
                break
            seen_offsets.add(offset)
            response = session.post(
                self.DEALER_URL,
                data={
                    "offset": offset,
                    "pincode": pincode,
                    "state_id": state_id,
                    "city_id": city_id,
                    "_token": token,
                },
                headers=ajax_headers,
                timeout=max(self.timeout, 20),
            )
            response.raise_for_status()
            payload = response.json()
            html = payload.get("html_receive") or ""
            for card in self._soup_cards(BeautifulSoup(html, "html.parser")):
                record = self._parse_card(card, category, state, city, pincode)
                if record is not None:
                    records.append(record)

            next_offset = self._next_offset(payload.get("btn_html") or "")
            if not next_offset:
                break
            offset = next_offset

        unique = []
        seen = set()
        for record in records:
            key = (record.name.casefold(), record.address.casefold(), record.phone)
            if key not in seen:
                seen.add(key)
                unique.append(record)
        return unique

    def _scrape_browser(
        self, category: str, state: str, city: str, pincode: str, *, headless: bool
    ):
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

        driver = None
        stage = "starting Chrome"
        try:
            print(f"[Dr Fixit] Opening locator for {city}, {state} {pincode}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading Dr Fixit locator"
            driver.get(self.LOCATOR_URL)
            wait.until(EC.presence_of_element_located((By.ID, "state")))

            stage = f"selecting state '{state}'"
            self._select_by_text(driver, wait, "state", state)

            stage = f"selecting city '{city}'"
            wait.until(lambda browser: len(browser.find_elements(By.CSS_SELECTOR, "#city option")) > 1)
            self._select_by_text(driver, wait, "city", city)

            stage = f"typing pincode '{pincode}'"
            pin = wait.until(EC.presence_of_element_located((By.ID, "pincode")))
            pin.clear()
            pin.send_keys(pincode)

            stage = "submitting Dr Fixit dealer search"
            submit = wait.until(EC.element_to_be_clickable((By.ID, "dealer_submit")))
            driver.execute_script("arguments[0].click();", submit)

            stage = "waiting for Dr Fixit dealer cards"
            wait.until(lambda browser: self._card_elements(browser) or self._no_results(browser))
            time.sleep(2)
            self._click_load_more_until_done(driver, wait)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            records = [
                record
                for card in self._soup_cards(soup)
                if (record := self._parse_card(card, category, state, city, pincode)) is not None
            ]
            print(f"[Dr Fixit] Parsed {len(records)} dealer cards")
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
    def _select_by_text(driver, wait, select_id: str, requested: str) -> None:
        from selenium.webdriver.common.by import By

        select = wait.until(lambda browser: browser.find_element(By.ID, select_id))

        def matching_option(browser):
            for option in browser.find_elements(By.CSS_SELECTOR, f"#{select_id} option"):
                label = (option.get_attribute("textContent") or "").strip()
                value = (option.get_attribute("value") or "").strip()
                if requested.casefold() in {label.casefold(), value.casefold()}:
                    return option
            return False

        option = wait.until(matching_option)
        driver.execute_script(
            "arguments[0].value=arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
            select,
            option.get_attribute("value"),
        )

    @staticmethod
    def _card_elements(driver):
        from selenium.webdriver.common.by import By

        return [
            card for card in driver.find_elements(By.CSS_SELECTOR, "#request_list > *, .cards-wrapper > *")
            if card.is_displayed() and card.text.strip()
        ]

    @staticmethod
    def _soup_cards(soup):
        return [
            card for card in soup.select(".card-box, #request_list > *, .cards-wrapper > *")
            if card.get_text(" ", strip=True)
        ]

    @staticmethod
    def _no_results(driver) -> bool:
        text = driver.page_source.casefold()
        return "no dealer" in text or "no result" in text or "not found" in text

    def _click_load_more_until_done(self, driver, wait) -> None:
        for _ in range(40):
            cards_before = len(self._card_elements(driver))
            button = self._visible_load_more(driver)
            if button is None:
                break
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
            driver.execute_script("arguments[0].click();", button)
            wait.until(lambda browser: len(self._card_elements(browser)) > cards_before or self._visible_load_more(browser) is None)
            time.sleep(1)

    @staticmethod
    def _visible_load_more(driver):
        from selenium.webdriver.common.by import By

        for element in driver.find_elements(By.CSS_SELECTOR, "#loadmorebtn button, #loadmorebtn a, button, a"):
            if element.is_displayed() and "load more" in element.text.strip().casefold():
                return element
        return None

    def _parse_card(self, card, category: str, state: str, city: str, pincode: str):
        lines = [line.strip() for line in card.get_text("\n", strip=True).splitlines() if line.strip()]
        if not lines:
            return None
        name_node = card.select_one("h1, h2, h3, h4, h5, .dealer-name, .store-name")
        name = name_node.get_text(" ", strip=True) if name_node else lines[0]
        text = " ".join(lines)
        phone = self._first_match(r"(?:\+91[\s-]?)?[6-9]\d{9}", text)
        email = self._first_match(r"[\w.+-]+@[\w.-]+\.\w+", text)
        found_pincode = self._first_match(r"\b[1-9]\d{5}\b", text) or pincode
        map_link = card.select_one("a[href*='maps'], a[href*='google']")
        ignored = {name.casefold(), phone.casefold(), email.casefold(), "get direction", "get directions"}
        address = ", ".join(line for line in lines[1:] if line.casefold() not in ignored)

        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name),
            phone=self._normalize(phone),
            email=self._normalize(email),
            address=self._normalize(address),
            city=city,
            state=state,
            pincode=found_pincode,
            dealer_type="Authorized Dealer",
            map_url=map_link.get("href") if map_link else None,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _first_match(pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        if not match:
            return ""
        return match.group(1) if match.groups() else match.group(0)

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @classmethod
    def _find_option_value(cls, soup, select_id: str, requested: str) -> str:
        requested_norm = cls._norm(requested)
        for option in soup.select(f"#{select_id} option"):
            value = (option.get("value") or "").strip()
            label = option.get_text(" ", strip=True)
            value_norm = cls._norm(value)
            label_norm = cls._norm(label)
            if (
                requested_norm in {value_norm, label_norm}
                or bool(label_norm and requested_norm in label_norm)
                or bool(label_norm and label_norm in requested_norm)
            ):
                return value
        return ""

    @classmethod
    def _find_city_id(cls, cities, requested: str) -> str:
        requested_norm = cls._norm(requested)
        for city in cities or []:
            value = str(city.get("id") or "").strip()
            label = str(city.get("title") or "").strip()
            label_norm = cls._norm(label)
            if (
                requested_norm == label_norm
                or bool(label_norm and requested_norm in label_norm)
                or bool(label_norm and label_norm in requested_norm)
            ):
                return value
        return ""

    @staticmethod
    def _next_offset(html: str) -> str:
        match = re.search(r'data-offset\s*=\s*["\']?(\d+)', html or "", re.I)
        return match.group(1) if match else ""

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "dr_fixit_error.png"))
            (output / "dr_fixit_error.html").write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass
