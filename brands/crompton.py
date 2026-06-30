"""Crompton dealer locator scraped from its rendered Shopify widget."""

import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class CromptonHandler(BaseBrandHandler):
    BRAND_NAME = "Crompton"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://www.crompton.co.in/pages/store-locator"
    CITY_ALIASES = {"bengaluru": "Bangalore"}

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not state or not city:
            raise ValueError("State and city are required for Crompton.")
        locator_city = self.CITY_ALIASES.get(city.casefold(), city)

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
                print(f"[Crompton] {failures[-1]}")

        raise RuntimeError(
            "Crompton browser scraper failed after headless and visible attempts. "
            + " | ".join(failures)
            + " Diagnostic files: output/crompton_error.png and output/crompton_error.html"
        )

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

        driver = None
        stage = "starting Chrome"
        try:
            mode = "headless" if headless else "visible"
            print(f"[Crompton] Opening {mode} locator for {city}, {state}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 40))
            stage = "loading the Crompton locator widget"
            driver.get(self.LOCATOR_URL)
            self._dismiss_cookie_consent(driver)

            wait.until(
                lambda browser: browser.find_elements(By.CSS_SELECTOR, ".psl_inner_item")
                or browser.find_elements(By.CSS_SELECTOR, "input#pac-input")
            )

            stage = "waiting for the Crompton widget to become ready"
            search_button = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button.mapSearchBttn"))
            )
            wait.until(
                lambda browser: search_button.text.strip().casefold() not in {"", "loading...", "loading"}
                and search_button.is_enabled()
            )
            # The app builds its city/category controls in several deferred
            # callbacks even after the button label changes.
            time.sleep(5)
            self._dismiss_cookie_consent(driver)

            stage = f"searching for city '{city}'"
            city_select_element = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "select.CitySelect"))
            )
            wait.until(
                lambda browser: any(
                    option.get_attribute("value").strip().casefold() == city.casefold()
                    for option in browser.find_elements(
                        By.CSS_SELECTOR, "select.CitySelect option"
                    )
                )
            )
            city_option = next(
                (
                    option
                    for option in driver.find_elements(
                        By.CSS_SELECTOR, "select.CitySelect option"
                    )
                    if option.get_attribute("value").strip().casefold()
                    == city.casefold()
                ),
                None,
            )
            if city_option is None:
                raise ValueError(f"Crompton city not found: {city}")
            city_value = city_option.get_attribute("value")
            driver.execute_script(
                "arguments[0].value=arguments[1];"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                city_select_element,
                city_value,
            )

            fan_filters = driver.find_elements(
                By.XPATH,
                "//input[contains(concat(' ',normalize-space(@class),' '),' tagCheckBox ') "
                "and translate(@value,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ')='FANS']",
            )
            if fan_filters and not fan_filters[0].is_selected():
                driver.execute_script("arguments[0].click();", fan_filters[0])

            driver.execute_script("arguments[0].click();", search_button)

            stage = "waiting for Crompton dealer cards"
            try:
                WebDriverWait(driver, 8).until(
                    EC.visibility_of_element_located(
                        (By.CSS_SELECTOR, ".location_details .spinner-border")
                    )
                )
            except Exception:
                pass
            # This Shopify app replaces the card list asynchronously and can
            # briefly hide its spinner between multiple rendering callbacks.
            time.sleep(10)
            wait.until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, ".location_details .spinner-border")
                )
            )
            wait.until(lambda browser: self._card_elements(browser))
            time.sleep(2)

            stage = "collecting all Crompton result pages"
            records = self._collect_all_pages(driver, wait, category, state, city)
            print(f"[Crompton] Parsed {len(records)} dealer cards")
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
    def _dismiss_cookie_consent(driver) -> None:
        """Dismiss Shopify's consent banner when it is present."""
        from selenium.webdriver.common.by import By

        selectors = (
            "#shopify-pc__banner__btn-accept",
            "button[id*='accept']",
        )
        for selector in selectors:
            for button in driver.find_elements(By.CSS_SELECTOR, selector):
                if button.is_displayed():
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(1)
                    return
        for button in driver.find_elements(
            By.XPATH,
            "//button[translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='accept']",
        ):
            if button.is_displayed():
                driver.execute_script("arguments[0].click();", button)
                time.sleep(1)
                return

    @staticmethod
    def _card_elements(driver):
        from selenium.webdriver.common.by import By

        for selector in (
            ".location_details .inner-item",
            ".store-marker",
            "#results-slt .inner-item",
        ):
            cards = driver.find_elements(By.CSS_SELECTOR, selector)
            if cards:
                return cards
        return []

    @staticmethod
    def _soup_cards(soup):
        for selector in (
            ".location_details .inner-item",
            ".store-marker",
            "#results-slt .inner-item",
        ):
            cards = soup.select(selector)
            if cards:
                return cards
        return []

    def _collect_all_pages(self, driver, wait, category, state, city):
        """Click Next until Crompton renders no enabled next-page link."""
        from bs4 import BeautifulSoup
        from selenium.webdriver.common.by import By

        records = []
        seen_records = set()
        seen_pages = set()
        page_number = 1

        while True:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = self._soup_cards(soup)
            if not cards:
                raise RuntimeError(
                    f"page {page_number} finished without rendering dealer cards"
                )

            page_signature = tuple(
                card.get_text(" ", strip=True)[:300] for card in cards[:3]
            )
            if page_signature in seen_pages:
                break
            seen_pages.add(page_signature)

            added = 0
            for card in cards:
                record = self._parse_card(card, category, state, city)
                if record is None:
                    continue
                key = (record.name.casefold(), record.phone, record.address.casefold())
                if key not in seen_records:
                    seen_records.add(key)
                    records.append(record)
                    added += 1
            print(
                f"[Crompton] Page {page_number}: {added} new records "
                f"({len(records)} total)"
            )

            next_link = self._enabled_next_link(driver)
            if next_link is None:
                break

            old_signature = self._live_page_signature(driver)
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});"
                "arguments[0].click();",
                next_link,
            )
            wait.until(
                lambda browser: self._live_page_signature(browser)
                != old_signature
            )
            wait.until(lambda browser: self._card_elements(browser))
            time.sleep(1)
            page_number += 1

        return records

    @staticmethod
    def _enabled_next_link(driver):
        from selenium.webdriver.common.by import By

        selectors = (
            ".Pslpagnation a[rel='next']",
            ".Pslpagnation a[aria-label*='Next']",
            ".Pslpagnation a[aria-label*='next']",
        )
        for selector in selectors:
            for link in driver.find_elements(By.CSS_SELECTOR, selector):
                classes = (link.get_attribute("class") or "").casefold()
                disabled = (link.get_attribute("aria-disabled") or "").casefold()
                if link.is_displayed() and "disabled" not in classes and disabled != "true":
                    return link

        for link in driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'Pslpagnation')]//a[contains(normalize-space(.),'Next')]",
        ):
            if link.is_displayed():
                return link
        return None

    @classmethod
    def _live_page_signature(cls, driver):
        return tuple(card.text[:300] for card in cls._card_elements(driver)[:3])

    def _parse_card(self, card, category: str, state: str, city: str):
        name = card.select_one(
            ".store-name, .location-name, .title, h2, h3, h4, h5"
        )
        phone = card.select_one("a[href^='tel:']")
        email = card.select_one("a[href^='mailto:']")
        map_link = card.select_one(
            "a[href*='google.com/maps'], a[href*='maps.google'], a.linkdetailstore"
        )
        address = card.select_one(
            ".psl_address_details_address .phone_email, "
            ".store-address, .address, .location-address, address"
        )
        dealer_type = card.select_one(
            ".psl_address_details_filter .phone_email"
        )

        text_lines = list(card.stripped_strings)
        name_text = name.get_text(" ", strip=True) if name else (text_lines[0] if text_lines else "")
        if address:
            address_text = address.get_text(" ", strip=True)
        else:
            ignored = {
                name_text.casefold(),
                phone.get_text(" ", strip=True).casefold() if phone else "",
                email.get_text(" ", strip=True).casefold() if email else "",
                "view details",
                "get directions",
            }
            address_text = ", ".join(
                line for line in text_lines[1:] if line.casefold() not in ignored
            )

        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name_text),
            phone=self._normalize(
                phone.get("href", "").removeprefix("tel:") if phone else ""
            ),
            email=self._normalize(
                email.get("href", "").removeprefix("mailto:") if email else ""
            ),
            address=self._normalize(address_text),
            city=city,
            state=state,
            dealer_type=self._normalize(
                dealer_type.get_text(" ", strip=True)
                if dealer_type
                else "Authorized Dealer"
            ),
            map_url=map_link.get("href") if map_link else None,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "crompton_error.png"))
            (output / "crompton_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
