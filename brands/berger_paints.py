"""Berger Paints dealer locator scraped through the rendered browser UI."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class BergerPaintsHandler(BaseBrandHandler):
    BRAND_NAME = "Berger Paints"
    SUPPORTED_CATEGORIES = ["cool roof"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://www.bergerpaints.com/dealer-locator"

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not city:
            raise ValueError("City is required for Berger Paints.")

        failures = []
        for headless in (True, False):
            try:
                return self._scrape_browser(category, state, city, headless=headless)
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Berger Paints] {failures[-1]}")

        raise RuntimeError(
            "Berger Paints browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/berger_paints_error.png and output/berger_paints_error.html"
        )

    def _scrape_browser(self, category: str, state: str, city: str, *, headless: bool):
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={self.session_headers['User-Agent']}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.geolocation": 2,
                "profile.managed_default_content_settings.geolocation": 2,
                "profile.default_content_setting_values.notifications": 2,
            },
        )

        driver = None
        stage = "starting Chrome"
        try:
            print(f"[Berger Paints] Opening locator for {city}")
            driver = webdriver.Chrome(options=options)
            self._deny_geolocation(driver)
            wait = WebDriverWait(driver, max(self.timeout, 40))

            stage = "loading Berger Paints locator"
            self._load_locator_page(driver, wait)
            self._wait_for_locator_mount(driver, wait)
            self._open_dealer_locator_from_support(driver)

            stage = f"typing city '{city}'"
            field = wait.until(lambda browser: self._search_field(browser))
            old_signature = self._result_signature(driver)
            selected_option = self._type_city_and_select_first(driver, wait, field, city)

            stage = "waiting for Berger Paints dealer cards"
            try:
                wait.until(
                    lambda browser: self._no_results(browser)
                    or (
                        self._card_elements(browser)
                        and self._result_signature(browser) != old_signature
                    )
                )
            except Exception:
                if selected_option:
                    raise
                wait.until(lambda browser: self._card_elements(browser) or self._no_results(browser))
            time.sleep(2)
            self._click_load_more_until_done(driver, wait)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            records = [
                record
                for card in self._soup_cards(soup)
                if (record := self._parse_card(card, category, state, city)) is not None
            ]
            if records and state and not self._records_match_location(records, state, city):
                self._save_diagnostics(driver)
                raise RuntimeError(
                    f"results did not update to {city}, {state}; "
                    "please check output/berger_paints_error.html"
                )
            print(f"[Berger Paints] Parsed {len(records)} dealer cards")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    @classmethod
    def _deny_geolocation(cls, driver) -> None:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "permission": {"name": "geolocation"},
                    "setting": "denied",
                    "origin": "https://www.bergerpaints.com",
                },
            )
        except Exception:
            pass

    @classmethod
    def _load_locator_page(cls, driver, wait) -> None:
        from selenium.webdriver.common.by import By

        for attempt in range(2):
            driver.get(cls.LOCATOR_URL)
            time.sleep(4)
            try:
                wait.until(lambda browser: browser.find_element(By.TAG_NAME, "body").text.strip())
            except Exception:
                pass
            body_text = driver.find_element(By.TAG_NAME, "body").text.casefold()
            if (
                "dealer" in body_text
                and (
                    "loading map" in body_text
                    or "get direction" in body_text
                    or "distance" in body_text
                    or driver.find_elements(By.CSS_SELECTOR, ".pac-target-input")
                )
            ):
                return
            if attempt == 0:
                driver.refresh()
                time.sleep(4)

    @staticmethod
    def _search_field(driver):
        from selenium.webdriver.common.by import By

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 3);")
        time.sleep(1)
        selectors = (
            ".pac-target-input",
            "input[aria-label*='Map']",
            "input[aria-label*='map']",
            "main input[placeholder*='City']",
            "main input[placeholder*='city']",
            "main input[placeholder*='Location']",
            "main input[placeholder*='location']",
            "main input[placeholder*='Search']",
            "main input[placeholder*='search']",
            "#__next main input[type='text']",
            "input[placeholder*='City']",
            "input[placeholder*='city']",
            "input[placeholder*='Location']",
            "input[placeholder*='location']",
            "input[placeholder*='Search']",
            "input[placeholder*='search']",
            "input[type='search']",
            "input[type='text']",
        )
        for selector in selectors:
            for field in driver.find_elements(By.CSS_SELECTOR, selector):
                placeholder = (field.get_attribute("placeholder") or "").casefold()
                classes = (field.get_attribute("class") or "").casefold()
                location_hint = any(
                    hint in placeholder or hint in classes
                    for hint in ("city", "location", "dealer", "store", "pin")
                )
                if not location_hint:
                    continue
                if field.is_displayed() and field.is_enabled():
                    return field
        return False

    @staticmethod
    def _wait_for_locator_mount(driver, wait) -> None:
        from selenium.webdriver.common.by import By

        def mounted(browser):
            browser.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            inputs = [
                element for element in browser.find_elements(By.CSS_SELECTOR, "main input, .pac-target-input")
                if element.is_displayed()
            ]
            if inputs:
                return True
            body = browser.find_element(By.TAG_NAME, "body").text.casefold()
            return "loading map" not in body and "dealer locator" in body

        try:
            wait.until(mounted)
        except Exception:
            pass

    @staticmethod
    def _open_dealer_locator_from_support(driver) -> None:
        from selenium.webdriver.common.by import By

        for link in driver.find_elements(By.XPATH, "//a[contains(@href, 'dealer-locator')]"):
            if link.is_displayed():
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
                    driver.execute_script("arguments[0].click();", link)
                    time.sleep(3)
                    return
                except Exception:
                    pass

    @staticmethod
    def _type_city_and_select_first(driver, wait, field, city: str) -> bool:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
            field,
        )
        time.sleep(1)
        driver.execute_script("window.scrollBy(0, -120);")
        time.sleep(0.5)
        driver.execute_script(
            "arguments[0].focus();"
            "arguments[0].click();",
            field,
        )
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(city)
        try:
            option = wait.until(
                lambda browser: next(
                    (
                        item
                        for item in browser.find_elements(
                            By.CSS_SELECTOR,
                            ".pac-container .pac-item, [role='listbox'] [role='option'], [role='option']",
                        )
                        if item.is_displayed() and item.text.strip()
                    ),
                    False,
                )
            )
            driver.execute_script(
                """
                const item = arguments[0];
                const rect = item.getBoundingClientRect();
                const x = rect.left + Math.min(rect.width / 2, 40);
                const y = rect.top + rect.height / 2;
                for (const type of ['mousedown', 'mouseup', 'click']) {
                  item.dispatchEvent(new MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: x,
                    clientY: y
                  }));
                }
                """,
                option,
            )
            time.sleep(1)
            if any(
                item.is_displayed()
                for item in driver.find_elements(By.CSS_SELECTOR, ".pac-container .pac-item")
            ):
                field.send_keys(Keys.ARROW_DOWN)
                field.send_keys(Keys.ENTER)
            wait.until(lambda browser: not any(
                item.is_displayed()
                for item in browser.find_elements(By.CSS_SELECTOR, ".pac-container .pac-item")
            ))
            time.sleep(4)
            return True
        except Exception:
            pass
        field.send_keys(Keys.ARROW_DOWN)
        field.send_keys(Keys.ENTER)
        time.sleep(3)
        return False

    @staticmethod
    def _card_elements(driver):
        from selenium.webdriver.common.by import By

        selectors = (
            "li[class*='DealerLocator_boxPart']",
            "[class*='DealerLocator_boxPart']",
            "[class*='DealerLocator_cardBody']",
            "[class*='dealer'] [class*='card']",
            "[class*='store'] [class*='card']",
            "[class*='locator'] [class*='card']",
            ".card",
            "article",
        )
        for selector in selectors:
            cards = [
                card for card in driver.find_elements(By.CSS_SELECTOR, selector)
                if card.is_displayed()
                and card.text.strip()
                and ("get direction" in card.text.casefold() or "distance" in card.text.casefold())
            ]
            if cards:
                return cards
        return []

    @classmethod
    def _result_signature(cls, driver):
        return tuple(card.text[:300] for card in cls._card_elements(driver)[:3])

    @staticmethod
    def _soup_cards(soup):
        for selector in (
            "li[class*='DealerLocator_boxPart']",
            "[class*='DealerLocator_boxPart']",
            "[class*='DealerLocator_cardBody']",
            "[class*='dealer'] [class*='card']",
            "[class*='store'] [class*='card']",
            "[class*='locator'] [class*='card']",
            ".card",
            "article",
        ):
            cards = [
                card for card in soup.select(selector)
                if card.get_text(" ", strip=True)
                and (
                    "get direction" in card.get_text(" ", strip=True).casefold()
                    or "distance" in card.get_text(" ", strip=True).casefold()
                )
            ]
            if cards:
                return cards
        return []

    @staticmethod
    def _no_results(driver) -> bool:
        text = driver.page_source.casefold()
        return "no dealer" in text or "no store" in text or "no result" in text

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

        for element in driver.find_elements(By.CSS_SELECTOR, "button, a"):
            text = element.text.strip().casefold()
            if element.is_displayed() and ("load more" in text or "show more" in text):
                return element
        return None

    def _parse_card(self, card, category: str, state: str, city: str):
        lines = [line.strip() for line in card.get_text("\n", strip=True).splitlines() if line.strip()]
        if not lines:
            return None
        name_node = card.select_one("h1, h2, h3, h4, h5, [class*='name'], [class*='title']")
        name = name_node.get_text(" ", strip=True) if name_node else lines[0]
        text = " ".join(lines)
        phone = self._first_match(r"(?:\+91[\s-]?)?[6-9]\d{9}", text)
        email = self._first_match(r"[\w.+-]+@[\w.-]+\.\w+", text)
        pincode = self._first_match(r"\b[1-9]\d{5}\b", text)
        map_link = card.select_one("a[href*='maps'], a[href*='google']")
        ignored = {
            name.casefold(),
            phone.casefold(),
            email.casefold(),
            "get direction",
            "get directions",
            "direction",
            "distance -",
        }
        address = ", ".join(
            line for line in lines[1:]
            if line.casefold() not in ignored
            and not re.search(r"\b\d+(?:\.\d+)?\s*km\b", line, re.IGNORECASE)
        )

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
            dealer_type="Authorized Dealer",
            map_url=map_link.get("href") if map_link else None,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _records_match_location(records, state: str, city: str) -> bool:
        expected = [value.casefold() for value in (state, city) if value]
        haystack = "\n".join(
            f"{record.name} {record.address}"
            for record in records
        ).casefold()
        return any(value in haystack for value in expected)

    @staticmethod
    def _first_match(pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        return match.group(0) if match else ""

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "berger_paints_error.png"))
            (output / "berger_paints_error.html").write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass
