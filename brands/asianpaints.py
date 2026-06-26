"""Asian Paints store locator scraped through the rendered browser UI."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class AsianPaintsHandler(BaseBrandHandler):
    BRAND_NAME = "Asian Paints"
    SUPPORTED_CATEGORIES = ["cool roof"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://www.asianpaints.com/store-locator.html"

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not city:
            raise ValueError("City is required for Asian Paints.")

        failures = []
        for headless in (True, False):
            try:
                return self._scrape_browser(category, state, city, headless=headless)
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Asian Paints] {failures[-1]}")

        raise RuntimeError(
            "Asian Paints browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/asianpaints_error.png and output/asianpaints_error.html"
        )

    def _scrape_browser(self, category: str, state: str, city: str, *, headless: bool):
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        options = self._chrome_options(headless)
        driver = None
        stage = "starting Chrome"
        try:
            print(f"[Asian Paints] Opening locator for {city}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading Asian Paints locator"
            driver.get(self.LOCATOR_URL)

            stage = f"searching city '{city}'"
            search = wait.until(EC.presence_of_element_located((By.ID, "store-search-input")))
            self._type_and_choose_first(driver, search, city)
            self._click_search_icon(driver)

            stage = "waiting for Asian Paints store cards"
            wait.until(lambda browser: self._card_elements(browser) or self._no_results(browser))
            time.sleep(2)

            stage = "clicking Asian Paints load more until exhausted"
            self._click_load_more_until_done(driver, wait)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            records = [
                record
                for card in self._soup_cards(soup)
                if (record := self._parse_card(card, category, state, city)) is not None
            ]
            if not records:
                self._save_diagnostics(driver)
            print(f"[Asian Paints] Parsed {len(records)} store cards")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _chrome_options(self, headless: bool):
        from selenium import webdriver

        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={self.session_headers['User-Agent']}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        return options

    @staticmethod
    def _type_and_choose_first(driver, field, value: str) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
        field.clear()
        field.send_keys(value)
        time.sleep(2)
        options = driver.find_elements(
            By.CSS_SELECTOR,
            ".pac-item, .dropdown-results li, .dropdown-results div, [role='option']",
        )
        for option in options:
            if option.is_displayed() and option.text.strip():
                driver.execute_script("arguments[0].click();", option)
                return
        field.send_keys(Keys.ARROW_DOWN)
        field.send_keys(Keys.ENTER)

    @staticmethod
    def _click_search_icon(driver) -> None:
        from selenium.webdriver.common.by import By

        for selector in (
            ".store-search-container .search-icon",
            ".search-input-container .search-icon",
        ):
            for button in driver.find_elements(By.CSS_SELECTOR, selector):
                if button.is_displayed():
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(2)
                    return

    @staticmethod
    def _card_elements(driver):
        from selenium.webdriver.common.by import By

        selectors = (
            ".storedetails-stores > *",
            "#dealer-container > *",
            ".store-card",
            ".store-detail-card",
            "[class*='store'][class*='card']",
            "[class*='dealer'][class*='card']",
        )
        for selector in selectors:
            cards = [
                card for card in driver.find_elements(By.CSS_SELECTOR, selector)
                if card.is_displayed()
                and card.text.strip()
                and any(token in card.text.casefold() for token in ("address", "direction", "call", "phone", "store"))
            ]
            if cards:
                return cards
        return []

    @staticmethod
    def _soup_cards(soup):
        for selector in (
            ".storedetails-stores > *",
            "#dealer-container > *",
            ".store-card",
            ".store-detail-card",
            "[class*='store'][class*='card']",
            "[class*='dealer'][class*='card']",
        ):
            cards = [
                card for card in soup.select(selector)
                if card.get_text(" ", strip=True)
                and any(
                    token in card.get_text(" ", strip=True).casefold()
                    for token in ("address", "direction", "call", "phone", "store")
                )
            ]
            if cards:
                return cards
        return []

    @staticmethod
    def _no_results(driver) -> bool:
        return "no store found" in driver.page_source.casefold()

    def _click_load_more_until_done(self, driver, wait) -> None:
        seen_counts = set()
        for _ in range(40):
            cards = self._card_elements(driver)
            count = len(cards)
            if count in seen_counts:
                break
            seen_counts.add(count)
            button = self._visible_load_more(driver)
            if button is None:
                break
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
            driver.execute_script("arguments[0].click();", button)
            wait.until(lambda browser: len(self._card_elements(browser)) > count or self._visible_load_more(browser) is None)
            time.sleep(1)

    @staticmethod
    def _visible_load_more(driver):
        from selenium.webdriver.common.by import By

        for selector in (
            ".load-more-cta:not(.d-none) a",
            ".load-more-cta:not(.d-none)",
            "a.explore-dealers:not(.d-none)",
            "button",
            "a",
        ):
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = element.text.strip().casefold()
                if element.is_displayed() and ("load more" in text or "explore more" in text):
                    return element
        return None

    def _parse_card(self, card, category: str, state: str, city: str):
        lines = [line.strip() for line in card.get_text("\n", strip=True).splitlines() if line.strip()]
        if not lines:
            return None

        name_node = card.select_one("h1, h2, h3, h4, h5, .store-name, .dealer-name")
        name = name_node.get_text(" ", strip=True) if name_node else lines[0]
        phone = self._first_match(r"(?:\+91[\s-]?)?[6-9]\d{9}", " ".join(lines))
        email = self._first_match(r"[\w.+-]+@[\w.-]+\.\w+", " ".join(lines))
        pincode = self._first_match(r"\b[1-9]\d{5}\b", " ".join(lines))
        map_link = card.select_one("a[href*='maps'], a[href*='google']")
        address = ", ".join(self._address_lines(lines[1:], name, phone, email))

        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name),
            phone=self._normalize(phone),
            email=self._normalize(email),
            address=self._normalize(address),
            city=city,
            state=state,
            pincode=pincode,
            dealer_type="Authorized Store",
            map_url=map_link.get("href") if map_link else None,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _first_match(pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        return match.group(0) if match else ""

    @classmethod
    def _address_lines(cls, lines, name: str, phone: str, email: str) -> list[str]:
        ignored_exact = {
            name.casefold(),
            phone.casefold(),
            email.casefold(),
            "address",
            "get direction",
            "get directions",
            "directions",
            "direction",
            "view details",
            "call",
            "phone",
            "contact",
            "store",
        }
        cleaned = []
        for line in lines:
            value = re.sub(r"\s+", " ", str(line or "")).strip(" ,:-")
            lower = value.casefold()
            if not value or lower in ignored_exact:
                continue
            if cls._first_match(r"(?:\+91[\s-]?)?[6-9]\d{9}", value):
                continue
            if cls._first_match(r"[\w.+-]+@[\w.-]+\.\w+", value):
                continue
            if re.search(r"\b\d+(?:\.\d+)?\s*km\b", value, re.IGNORECASE):
                continue
            if any(token in lower for token in ("get direction", "view detail", "call now", "open now", "closed now")):
                continue
            cleaned.append(value)
        return cleaned

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "asianpaints_error.png"))
            (output / "asianpaints_error.html").write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass
