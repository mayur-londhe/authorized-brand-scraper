"""Hindware dealer locator using its rendered React interface."""

import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class HindwareHandler(BaseBrandHandler):
    BRAND_NAME = "Hindware"
    SUPPORTED_CATEGORIES = [
        "water efficient fixtures",
        "sanitaryware",
        "faucets",
        "showers",
        "tiles",
    ]
    SOURCE_URL = "https://hindware.com/store-locator"

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        """Try headless Chrome first, then visible Chrome for anti-bot pages."""
        state = self._normalize(state)
        city = self._normalize(city)
        if not state:
            raise ValueError("State is required for Hindware.")

        failures = []
        for headless in (True,):
            try:
                return self._scrape_browser(category, state, city, headless=headless)
            except Exception as exc:
                message = str(exc).splitlines()[0].strip() or "no error message"
                mode = "headless" if headless else "visible"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Hindware] {failures[-1]}")

        raise RuntimeError(
            "Hindware browser scraper failed after headless and visible attempts. "
            + " | ".join(failures)
            + " Diagnostic files: output/hindware_error.png and output/hindware_error.html"
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
            print(f"[Hindware] Opening {mode} locator for {state} / {city or 'all cities'}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 25))
            stage = "loading the locator page"
            driver.get(self.SOURCE_URL)

            stage = f"selecting state '{state}'"
            self._select_react_option(driver, wait, "formState", state)
            if city:
                stage = f"selecting city '{city}'"
                self._select_react_option(driver, wait, "formCity", city)

            stage = "finding the Search Store button"
            search_button = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[normalize-space()='Search Store']")
                )
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_button)
            driver.execute_script("arguments[0].click();", search_button)

            # The initial page already says "No Stores Found", so that text cannot
            # be used as an immediate completion signal. Wait for React's spinner
            # lifecycle when visible, then allow the result DOM to settle.
            stage = "waiting for Hindware search results"
            try:
                short_wait = WebDriverWait(driver, 5)
                short_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".spinner-border")))
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".spinner-border")))
            except Exception:
                time.sleep(4)
            wait.until(
                lambda browser: browser.find_elements(By.CSS_SELECTOR, ".store-loc-col")
                or "No Stores Found" in browser.page_source
            )
            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select(".store-loc .store-loc-col, .store-loc-col")
            print(f"[Hindware] Found {len(cards)} rendered dealer cards")
            records = [
                record
                for card in cards
                if (record := self._parse_card(card, category, state, city)) is not None
            ]
            # Never export the locator's initial all-India result set if a UI
            # selection silently fails. Hindware includes city/state in address.
            expected = [value.casefold() for value in (city, state) if value]
            return [
                record
                for record in records
                if all(value in record.address.casefold() for value in expected)
            ]
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    @staticmethod
    def _save_diagnostics(driver) -> None:
        """Preserve the blocked/error page so the next failure is actionable."""
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "hindware_error.png"))
            (output / "hindware_error.html").write_text(
                driver.page_source,
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _select_react_option(driver, wait, form_id: str, label: str) -> None:
        """Choose a React-Select option using its stable form label."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as EC

        # React-Select's numeric input IDs change between renders/builds. The
        # associated form labels (formState/formCity) are stable.
        field_xpath = (
            f"(//label[@for='{form_id}']/following::input[@role='combobox'])[1]"
        )
        field = wait.until(EC.presence_of_element_located((By.XPATH, field_xpath)))
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});"
            "arguments[0].focus();"
            "arguments[0].click();",
            field,
        )
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(label)
        option_xpath = (
            "//*[@role='option' and "
            f"translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            f"'abcdefghijklmnopqrstuvwxyz')={label.casefold()!r}]"
        )
        try:
            option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
            driver.execute_script("arguments[0].click();", option)
        except Exception:
            field.send_keys(Keys.ENTER)

    def _parse_card(self, card, category: str, state: str, city: str):
        name = card.select_one(".store-title")
        if name is None:
            return None

        address = card.select_one(".store-address")
        email = card.select_one(".store-email a")
        phones = card.select(".store-tel a")
        brand = card.select_one(".brand-val")
        map_link = card.select_one(".store-cta a[href]")
        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name.get_text(" ", strip=True)),
            phone=", ".join(p.get_text(" ", strip=True) for p in phones),
            email=self._normalize(email.get_text(" ", strip=True) if email else ""),
            address=self._normalize(address.get_text(" ", strip=True) if address else ""),
            city=city,
            state=state,
            dealer_type=self._normalize(brand.get_text(" ", strip=True) if brand else "Showroom"),
            map_url=map_link.get("href") if map_link else None,
        )
        return record if record.is_valid() else None
