"""Atomberg store locator scraper."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class AtombergHandler(BaseBrandHandler):
    BRAND_NAME = "Atomberg"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = False
    REQUIRES_PINCODE = True

    LOCATOR_URL = "https://atomberg.com/store-locator"

    PINCODE_RE = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")
    PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")
    EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

    CARD_SELECTORS = (
        "[class*='store'][class*='card']",
        "[class*='dealer'][class*='card']",
        "[class*='locator'][class*='card']",
        "[class*='store-list'] > *",
        "[class*='dealer-list'] > *",
        "[class*='location-list'] > *",
        ".store-card",
        ".dealer-card",
        ".location-card",
        "li[class*='store']",
        "li[class*='dealer']",
    )

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
            raise ValueError("Pincode is required for Atomberg.")

        failures = []
        # Atomberg is prone to contact-detail modals and map overlays, so run
        # the visible browser first instead of spending a hidden headless
        # attempt that commonly times out before the user sees anything.
        for headless in (False,):
            mode = "headless" if headless else "visible"
            try:
                records = self._scrape_browser(
                    category, state, city, pincode, headless=headless
                )
                if records:
                    return records
                failures.append(f"{mode}: 0 rendered cards for pincode")
                print(f"[Atomberg] {failures[-1]}")
            except Exception as exc:
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Atomberg] {failures[-1]}")

        raise RuntimeError(
            "Atomberg browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/atomberg_error.png and output/atomberg_error.html"
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
        from selenium.webdriver.common.keys import Keys
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
            print(f"[Atomberg] Opening locator for pincode {pincode}")
            driver = webdriver.Chrome(options=options)
            self._deny_geolocation(driver)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading Atomberg locator"
            driver.get(self.LOCATOR_URL)
            time.sleep(4)
            self._close_modals(driver)
            self._dismiss_blocking_contact_modal(driver)
            initial_signature = self._card_signature(driver)

            stage = f"typing pincode '{pincode}'"
            field = wait.until(lambda browser: self._pincode_field(browser))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});"
                "arguments[0].focus(); arguments[0].click();",
                field,
            )
            field.send_keys(Keys.CONTROL, "a")
            field.send_keys(Keys.DELETE)
            field.clear()
            field.send_keys(pincode)
            if (field.get_attribute("value") or "").strip() != pincode:
                driver.execute_script("arguments[0].value = arguments[1];", field, pincode)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                field,
            )
            time.sleep(0.5)

            stage = "clicking Atomberg search button"
            search = wait.until(lambda browser: self._search_button(browser, field))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});"
                "arguments[0].click();",
                search,
            )
            time.sleep(1)
            self._close_modals(driver)
            self._dismiss_blocking_contact_modal(driver)

            stage = "waiting for Atomberg dealer cards"
            wait.until(
                lambda browser: self._dismiss_modal_and_get_cards(
                    browser, initial_signature
                )
            )
            self._close_modals(driver)
            self._dismiss_blocking_contact_modal(driver)
            time.sleep(2)

            records = self._parse_results(
                driver.page_source, category, state, city, pincode
            )
            print(f"[Atomberg] Parsed {len(records)} rendered cards")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _parse_results(
        self,
        html: str,
        category: str,
        state: str,
        city: str,
        input_pincode: str,
    ) -> List[DealerRecord]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        cards = self._soup_cards(soup)
        records = []
        seen = set()
        for card in cards:
            record = self._parse_card(card, category, state, city, input_pincode)
            if record is None:
                continue
            key = (record.name.casefold(), record.address.casefold(), record.phone)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
        return records

    def _parse_card(
        self,
        card,
        category: str,
        state: str,
        city: str,
        input_pincode: str,
    ):
        text_lines = [
            line.strip()
            for line in card.get_text("\n", strip=True).splitlines()
            if line.strip()
        ]
        if not text_lines:
            return None

        joined = " ".join(text_lines)
        if not (
            self.PHONE_RE.search(joined)
            or self.PINCODE_RE.search(joined)
            or "direction" in joined.casefold()
            or "dealer" in joined.casefold()
            or "store" in joined.casefold()
        ):
            return None

        labeled = self._labeled_fields(text_lines, joined)
        name_node = card.select_one(
            "h1, h2, h3, h4, h5, h6, [class*='name'], [class*='title']"
        )
        phone_node = card.select_one("a[href^='tel:']")
        email_node = card.select_one("a[href^='mailto:']")
        map_node = card.select_one(
            "a[href*='google.com/maps'], a[href*='maps.google'], a[href*='goo.gl/maps']"
        )

        name = (
            labeled.get("store name")
            or (name_node.get_text(" ", strip=True) if name_node else text_lines[0])
        )
        phone = (
            phone_node.get("href", "").removeprefix("tel:")
            if phone_node
            else labeled.get("phone") or self._first_match(self.PHONE_RE, joined)
        )
        email = (
            email_node.get("href", "").removeprefix("mailto:")
            if email_node
            else self._first_match(self.EMAIL_RE, joined)
        )
        pincode = self._first_match(self.PINCODE_RE, joined) or input_pincode

        ignored = {
            name.casefold(),
            str(phone).casefold(),
            str(email).casefold(),
            "get directions",
            "direction",
            "directions",
            "view on map",
        }
        address = labeled.get("address") or ", ".join(
            line
            for line in text_lines[1:]
            if line.casefold() not in ignored
            and not line.casefold().startswith(("store name:", "address:", "phone:"))
            and not self.PHONE_RE.fullmatch(line)
            and not self.EMAIL_RE.fullmatch(line)
        )

        lat, lon = self._coordinates_from_card(card)
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
            dealer_type="Atomberg Dealer",
            map_url=map_node.get("href") if map_node else None,
            latitude=lat,
            longitude=lon,
        )
        return record if record.is_valid() else None

    @classmethod
    def _pincode_field(cls, driver):
        from selenium.webdriver.common.by import By

        targeted = driver.execute_script(
            """
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 0 && rect.height > 0 &&
                style.visibility !== 'hidden' && style.display !== 'none';
            };
            const inputs = [...document.querySelectorAll('input')].filter(visible);
            const scored = inputs.map((input) => {
              const container = input.closest('form, section, div') || input.parentElement;
              const text = [
                container ? container.innerText : '',
                input.placeholder || '',
                input.name || '',
                input.id || '',
                input.type || ''
              ].join(' ').toLowerCase();
              let score = 0;
              if (text.includes('pincode') || text.includes('pin code')) score += 100;
              if (text.includes('postal')) score += 80;
              if (text.includes('dealer') || text.includes('store')) score += 20;
              if (input.type === 'number' || input.inputMode === 'numeric') score += 10;
              return {input, score};
            }).filter((item) => item.score >= 80)
              .sort((a, b) => b.score - a.score);
            return scored.length ? scored[0].input : null;
            """
        )
        if targeted is not None:
            return targeted

        selectors = (
            "input[name*='pin']",
            "input[id*='pin']",
            "input[placeholder*='Pincode']",
            "input[placeholder*='pincode']",
            "input[placeholder*='Pin Code']",
            "input[name*='postal']",
            "input[id*='postal']",
            "input[type='number']",
            "input[type='search']",
            "input[type='text']",
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
            nearby = driver.execute_script(
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
                return candidates.find((el) =>
                  visible(el) && /search|find|locate|submit/.test(label(el))
                ) || null;
                """,
                field,
            )
            if nearby is not None:
                return nearby

        for tag in ("button", "input", "a"):
            for element in driver.find_elements(By.TAG_NAME, tag):
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
                    and any(word in label for word in ("search", "find", "locate"))
                ):
                    return element
        return False

    @classmethod
    def _cards_updated_or_present(cls, driver, initial_signature):
        cards = cls._card_elements(driver)
        if not cards:
            return False
        signature = cls._card_signature(driver)
        if not initial_signature or signature != initial_signature:
            return cards
        return False

    @classmethod
    def _dismiss_modal_and_get_cards(cls, driver, initial_signature):
        cls._dismiss_blocking_contact_modal(driver)
        cls._close_contact_modal_with_script(driver)
        return cls._cards_updated_or_present(driver, initial_signature)

    @classmethod
    def _card_elements(cls, driver):
        from selenium.webdriver.common.by import By

        for selector in cls.CARD_SELECTORS:
            cards = [
                card
                for card in driver.find_elements(By.CSS_SELECTOR, selector)
                if card.is_displayed() and card.text.strip()
            ]
            if cards:
                return cards
        cards = [
            element
            for element in driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.),'Store Name:')]/ancestor::div["
                "contains(normalize-space(.),'Address:') and "
                "contains(normalize-space(.),'Phone:')][1]",
            )
            if element.is_displayed() and element.text.strip()
        ]
        if cards:
            return cards
        return []

    @classmethod
    def _card_signature(cls, driver):
        return tuple(card.text.strip()[:300] for card in cls._card_elements(driver)[:5])

    @classmethod
    def _soup_cards(cls, soup):
        for selector in cls.CARD_SELECTORS:
            cards = [
                card
                for card in soup.select(selector)
                if card.get_text(" ", strip=True)
            ]
            if cards:
                return cards
        cards = []
        for label in soup.find_all(string=re.compile(r"Store Name:", re.I)):
            parent = label.parent
            for _ in range(6):
                if parent is None:
                    break
                text = parent.get_text(" ", strip=True)
                if "Address:" in text and "Phone:" in text:
                    cards.append(parent)
                    break
                parent = parent.parent
        if cards:
            return cards
        return []

    @staticmethod
    def _coordinates_from_card(card) -> tuple[str, str]:
        for attr_lat, attr_lon in (
            ("data-lat", "data-lng"),
            ("data-latitude", "data-longitude"),
            ("data-lat", "data-long"),
        ):
            lat = card.get(attr_lat)
            lon = card.get(attr_lon)
            if lat and lon:
                return str(lat), str(lon)

        text = " ".join(
            str(value)
            for key, value in card.attrs.items()
            if key.startswith("data-") and isinstance(value, str)
        )
        match = re.search(r"(-?\d{1,2}\.\d+)[,\s]+(-?\d{1,3}\.\d+)", text)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    @staticmethod
    def _labeled_fields(lines: list[str], joined: str) -> dict[str, str]:
        fields = {}
        labels = {"store name", "address", "phone"}
        for index, line in enumerate(lines):
            match = re.match(r"^(Store Name|Address|Phone)\s*:?\s*(.*)$", line, re.I)
            if not match:
                continue
            key = match.group(1).casefold()
            value = match.group(2).strip()
            if not value and index + 1 < len(lines):
                value = lines[index + 1].strip()
            if value:
                fields[key] = value

        if {"store name", "address", "phone"} - set(fields):
            patterns = {
                "store name": r"Store Name:\s*(.*?)(?=\s+Address:|\s+Phone:|$)",
                "address": r"Address:\s*(.*?)(?=\s+Phone:|\s+Store Name:|$)",
                "phone": r"Phone:\s*([+\d][\d\s-]{7,})",
            }
            for key in labels - set(fields):
                match = re.search(patterns[key], joined, re.I)
                if match:
                    fields[key] = match.group(1).strip(" ,")
        return fields

    @classmethod
    def _deny_geolocation(cls, driver) -> None:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "permission": {"name": "geolocation"},
                    "setting": "denied",
                    "origin": "https://atomberg.com",
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
            "button[id*='close']",
            "button[class*='close']",
            "button[id*='reject']",
            "button[id*='decline']",
            "svg[class*='close']",
            "[class*='modal'] svg",
            "[role='dialog'] svg",
        )
        for _ in range(4):
            closed = False
            for selector in selectors:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    if element.is_displayed():
                        driver.execute_script("arguments[0].click();", element)
                        time.sleep(0.7)
                        closed = True
            closed = AtombergHandler._close_contact_modal_with_script(driver) or closed
            if not closed:
                break

    @staticmethod
    def _close_contact_modal_with_script(driver) -> bool:
        try:
            return bool(
                driver.execute_script(
                    """
                    const visible = (el) => {
                      const rect = el.getBoundingClientRect();
                      const style = window.getComputedStyle(el);
                      return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const dialogs = [...document.querySelectorAll('[role="dialog"], .modal, [class*="modal"], body > div')]
                      .filter((el) => visible(el) && /share contact details|phone number|nearest store/i.test(el.innerText || ''));
                    let closed = false;
                    for (const dialog of dialogs) {
                      const close = [...dialog.querySelectorAll('button, svg, span, div, a')]
                        .find((el) => {
                          const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().toLowerCase();
                          const rect = el.getBoundingClientRect();
                          const drect = dialog.getBoundingClientRect();
                          return visible(el) && (
                            text === 'x' || text === '×' || text.includes('close') ||
                            (rect.left > drect.right - 70 && rect.top < drect.top + 70)
                          );
                        });
                      if (close) {
                        close.click();
                        closed = true;
                      } else {
                        dialog.style.display = 'none';
                        dialog.setAttribute('aria-hidden', 'true');
                        closed = true;
                      }
                    }
                    for (const overlay of [...document.querySelectorAll('.modal-backdrop, .MuiBackdrop-root, [class*="overlay"], [class*="backdrop"]')]) {
                      if (visible(overlay)) {
                        overlay.style.display = 'none';
                        overlay.setAttribute('aria-hidden', 'true');
                        closed = true;
                      }
                    }
                    return closed;
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _dismiss_blocking_contact_modal(driver) -> bool:
        try:
            return bool(
                driver.execute_script(
                    """
                    const visible = (el) => {
                      const rect = el.getBoundingClientRect();
                      const style = window.getComputedStyle(el);
                      return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none' &&
                        style.opacity !== '0';
                    };
                    const label = (el) => [
                      el.innerText || '',
                      el.value || '',
                      el.placeholder || '',
                      el.getAttribute('aria-label') || '',
                      el.getAttribute('title') || '',
                      el.name || '',
                      el.id || ''
                    ].join(' ').toLowerCase();
                    const isContactDialog = (el) => {
                      if (!visible(el)) return false;
                      const text = label(el);
                      const inputs = [...el.querySelectorAll('input')].filter(visible);
                      const buttons = [...el.querySelectorAll('button, input[type="button"], input[type="submit"], a')].filter(visible);
                      const inputText = inputs.map(label).join(' ');
                      const buttonText = buttons.map(label).join(' ');
                      const hasNamePhone = /name/.test(inputText) && /phone|mobile/.test(inputText);
                      const hasSubmit = /submit/.test(buttonText);
                      const hasContactText = /share contact|contact details|nearest store|store-details|communication/.test(text);
                      return hasContactText || (inputs.length >= 2 && hasNamePhone) || (inputs.length >= 2 && hasSubmit);
                    };
                    const candidates = [...document.querySelectorAll('body *')]
                      .filter(isContactDialog)
                      .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                      });
                    if (!candidates.length) return false;

                    let dialog = candidates[0];
                    for (let node = dialog; node && node !== document.body; node = node.parentElement) {
                      const style = window.getComputedStyle(node);
                      const rect = node.getBoundingClientRect();
                      if (
                        (style.position === 'fixed' || style.position === 'absolute') &&
                        rect.width >= 250 && rect.height >= 200 &&
                        rect.width < window.innerWidth * 0.95 &&
                        rect.height < window.innerHeight * 0.95
                      ) {
                        dialog = node;
                        break;
                      }
                    }

                    const rect = dialog.getBoundingClientRect();
                    const closePoint = document.elementFromPoint(rect.right - 24, rect.top + 24);
                    if (closePoint) {
                      closePoint.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, view: window}));
                      closePoint.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, view: window}));
                      closePoint.click();
                    }
                    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true}));

                    const stillVisible = visible(dialog);
                    if (stillVisible) {
                      dialog.style.setProperty('display', 'none', 'important');
                      dialog.style.setProperty('visibility', 'hidden', 'important');
                      dialog.style.setProperty('pointer-events', 'none', 'important');
                      dialog.setAttribute('aria-hidden', 'true');
                    }

                    for (const el of [...document.querySelectorAll('body *')]) {
                      if (!visible(el) || el === dialog || dialog.contains(el)) continue;
                      const style = window.getComputedStyle(el);
                      const rect = el.getBoundingClientRect();
                      const z = Number.parseInt(style.zIndex || '0', 10);
                      const coversScreen = rect.width > window.innerWidth * 0.5 && rect.height > window.innerHeight * 0.5;
                      if ((style.position === 'fixed' || style.position === 'absolute') && coversScreen && z >= 10) {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('pointer-events', 'none', 'important');
                        el.setAttribute('aria-hidden', 'true');
                      }
                    }
                    document.body.style.overflow = 'auto';
                    document.documentElement.style.overflow = 'auto';
                    return true;
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _first_match(pattern, text: str) -> str:
        match = pattern.search(text or "")
        return match.group(1) if match and match.groups() else match.group(0) if match else ""

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "atomberg_error.png"))
            (output / "atomberg_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
