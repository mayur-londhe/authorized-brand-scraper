"""Nerolac Paints store locator scraper."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class NerolacHandler(BaseBrandHandler):
    BRAND_NAME = "Nerolac"
    SUPPORTED_CATEGORIES = ["cool roof"]
    REQUIRES_CITY = False
    REQUIRES_PINCODE = True

    LOCATOR_URL = "https://www.nerolac.com/store-locator"

    PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")
    PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")
    EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
    CARD_SELECTOR = "ul.locate-info-list > li div.info-list-block"

    def fetch(
        self,
        category: str,
        state: str,
        city: str = "",
        pincode: str = "",
    ) -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        pincode = self._normalize(pincode)
        if not pincode:
            raise ValueError("Pincode is required for Nerolac.")

        failures = []
        for headless in (True,):
            mode = "headless" if headless else "visible"
            try:
                records = self._scrape_browser(
                    category, state, city, pincode, headless=headless
                )
                if records:
                    return records
                failures.append(f"{mode}: 0 rendered dealers for pincode")
                print(f"[Nerolac] {failures[-1]}")
            except Exception as exc:
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Nerolac] {failures[-1]}")

        raise RuntimeError(
            "Nerolac scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/nerolac_error.png and output/nerolac_error.html"
        )

    def _scrape_browser(
        self,
        category: str,
        state: str,
        city: str,
        pincode: str,
        *,
        headless: bool,
    ) -> List[DealerRecord]:
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
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
            print(f"[Nerolac] Opening locator for pincode {pincode}")
            driver = webdriver.Chrome(options=options)
            self._deny_geolocation(driver)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading Nerolac locator"
            driver.get(self.LOCATOR_URL)
            time.sleep(3)
            self._close_modals(driver)
            initial_signature = self._card_signature(driver)

            stage = f"typing pincode '{pincode}'"
            field = wait.until(lambda browser: self._pincode_field(browser))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});"
                "arguments[0].focus();"
                "arguments[0].click();",
                field,
            )
            field.send_keys(Keys.CONTROL, "a")
            field.send_keys(Keys.DELETE)
            field.clear()
            field.send_keys(pincode)
            current_value = field.get_attribute("value") or ""
            if current_value.strip() != pincode:
                driver.execute_script("arguments[0].value = arguments[1];", field, pincode)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                field,
            )
            time.sleep(0.3)

            stage = "clicking Nerolac Search button"
            search = wait.until(lambda browser: self._search_button(browser, field))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});"
                "arguments[0].click();",
                search,
            )

            stage = "waiting for Nerolac rendered dealer cards"
            wait.until(
                lambda browser: self._rendered_cards_updated(
                    browser, initial_signature
                )
            )
            time.sleep(1)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select(self.CARD_SELECTOR)
            records = []
            seen = set()
            for card in cards:
                record = self._parse_card(card, category, state, city, pincode)
                if record is None:
                    continue
                key = (
                    record.name.casefold(),
                    record.address.casefold(),
                    record.phone,
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)

            print(
                f"[Nerolac] Parsed {len(records)} rendered dealer cards for {pincode}"
            )
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics_driver(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _parse_card(
        self,
        card,
        category: str,
        state: str,
        city: str,
        input_pincode: str,
    ):
        left = card.select_one(".info-list-info-left") or card
        lines = [
            line.strip()
            for line in left.get_text("\n", strip=True).splitlines()
            if line.strip()
        ]
        if not lines:
            return None

        name_node = card.select_one("h6, h5, h4")
        name = name_node.get_text(" ", strip=True) if name_node else lines[0]
        full_text = " ".join(lines)
        phone = self._first_match(self.PHONE_RE, full_text)
        email = self._first_match(self.EMAIL_RE, full_text)
        pincode = self._first_match(self.PINCODE_RE, full_text) or input_pincode

        address = ", ".join(self._address_lines(lines[1:], name, phone, email))

        map_link_node = card.select_one("a[href*='maps']")
        block = card if card.has_attr("data-lang") else card.select_one("[data-lang]")

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
            dealer_type="Nerolac Dealer",
            map_url=map_link_node.get("href") if map_link_node else None,
            latitude=block.get("data-lang") if block else None,
            longitude=block.get("data-long") if block else None,
        )
        return record if record.is_valid() else None

    @classmethod
    def _deny_geolocation(cls, driver) -> None:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "permission": {"name": "geolocation"},
                    "setting": "denied",
                    "origin": "https://www.nerolac.com",
                },
            )
        except Exception:
            pass

    @staticmethod
    def _close_modals(driver) -> None:
        from selenium.webdriver.common.by import By

        selectors = (
            "button[aria-label='Close']",
            "button[aria-label='close']",
            ".btn-close",
            ".modal .close",
            ".modal button.close",
            ".popup-close",
            ".mfp-close",
            "[data-dismiss='modal']",
            "[data-bs-dismiss='modal']",
            "button#onetrust-reject-all-handler",
        )
        for _ in range(3):
            closed = False
            for selector in selectors:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    if element.is_displayed():
                        driver.execute_script("arguments[0].click();", element)
                        time.sleep(1)
                        closed = True
            if not closed:
                break

    @staticmethod
    def _pincode_field(driver):
        from selenium.webdriver.common.by import By

        right_panel_field = driver.execute_script(
            """
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 0 && rect.height > 0 &&
                style.visibility !== 'hidden' && style.display !== 'none';
            };
            const inputs = [...document.querySelectorAll('input')].filter(visible);
            const viewportMid = window.innerWidth / 2;
            const scored = inputs
              .map((input) => {
                const rect = input.getBoundingClientRect();
                const container = input.closest('form, section, div') || input.parentElement;
                const text = (container ? container.innerText : '') + ' ' +
                  (input.placeholder || '') + ' ' +
                  (input.name || '') + ' ' +
                  (input.id || '');
                const lower = text.toLowerCase();
                let score = 0;
                if (lower.includes('pincode') || lower.includes('pin code')) score += 100;
                if (lower.includes('nearest dealer')) score += 30;
                if (lower.includes('search')) score += 20;
                if (rect.left > viewportMid) score += 15;
                if (rect.top < window.innerHeight * 0.85) score += 10;
                if (input.type === 'number' || input.inputMode === 'numeric') score += 5;
                return {input, score, left: rect.left, top: rect.top};
              })
              .filter((item) => item.score >= 100)
              .sort((a, b) => b.score - a.score || b.left - a.left || a.top - b.top);
            return scored.length ? scored[0].input : null;
            """
        )
        if right_panel_field is not None:
            return right_panel_field

        selectors = (
            "input[name='postal_code']",
            "input[name*='pin']",
            "input[id*='pin']",
            "input[id*='postal']",
            "input[name*='postal']",
            "input[placeholder*='Pincode']",
            "input[placeholder*='pincode']",
            "input[placeholder*='Pin Code']",
            "input[placeholder*='pin code']",
            "input[type='number']",
            "input[type='text']",
            "input[type='search']",
        )
        for selector in selectors:
            for field in driver.find_elements(By.CSS_SELECTOR, selector):
                if field.is_displayed() and field.is_enabled():
                    return field
        return False

    @staticmethod
    def _search_button(driver, field=None):
        from selenium.webdriver.common.by import By

        if field is not None:
            panel_search = driver.execute_script(
                """
                const field = arguments[0];
                const visible = (el) => {
                  const rect = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
                };
                const label = (el) => (
                  el.innerText || el.value || el.getAttribute('aria-label') ||
                  el.getAttribute('title') || ''
                ).trim().toLowerCase();
                const candidates = [];
                let node = field;
                for (let i = 0; node && i < 8; i += 1, node = node.parentElement) {
                  candidates.push(...node.querySelectorAll('button, input[type="button"], a'));
                }
                const search = candidates.find((el) => visible(el) && label(el).includes('search'));
                return search || null;
                """,
                field,
            )
            if panel_search is not None:
                return panel_search

        selectors = (
            "button[type='button']",
            "input[type='button']",
            "a.btn",
            "button",
            "input",
            "a",
        )
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                label = (
                    element.text
                    or element.get_attribute("value")
                    or element.get_attribute("aria-label")
                    or element.get_attribute("title")
                    or ""
                ).strip().casefold()
                if (
                    element.is_displayed()
                    and element.is_enabled()
                    and "search" in label
                ):
                    return element
        return False

    @classmethod
    def _rendered_cards_updated(cls, driver, initial_signature):
        cards = driver.find_elements("css selector", cls.CARD_SELECTOR)
        if not cards:
            return False

        signature = cls._card_signature(driver)
        if not initial_signature or signature != initial_signature:
            return cards
        return False

    @classmethod
    def _card_signature(cls, driver):
        cards = driver.find_elements("css selector", cls.CARD_SELECTOR)
        if not cards:
            return ()
        return tuple(card.text.strip() for card in cards[:5])

    @staticmethod
    def _first_match(pattern, text: str) -> str:
        match = pattern.search(text)
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
        }
        cleaned = []
        for line in lines:
            value = re.sub(r"\s+", " ", str(line or "")).strip(" ,:-")
            lower = value.casefold()
            if not value or lower in ignored_exact:
                continue
            if cls.PHONE_RE.search(value) or cls.EMAIL_RE.search(value):
                continue
            if re.search(r"\b\d+(?:\.\d+)?\s*km\b", value, re.IGNORECASE):
                continue
            if any(token in lower for token in ("get direction", "view detail", "call now", "open now", "closed now")):
                continue
            cleaned.append(value)
        return cleaned

    @staticmethod
    def _save_diagnostics_driver(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "nerolac_error.png"))
            (output / "nerolac_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
