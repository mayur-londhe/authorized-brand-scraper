"""Usha Bangalore stores scraped from the browser-rendered city results."""

import re
import time
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class UshaHandler(BaseBrandHandler):
    BRAND_NAME = "Usha"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]

    LOCATOR_URL = "https://ushafans.com/find-city-store"
    LOCATOR_CITY = "Bangalore"

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        """Select Bangalore only; leave the store-type dropdown at its default."""
        failures = []
        for headless in (True, False):
            try:
                return self._scrape_browser(
                    category, self._normalize(state), headless=headless
                )
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Usha] {failures[-1]}")
        raise RuntimeError(
            "Usha scraper failed after headless and visible attempts. "
            + " | ".join(failures)
            + " Diagnostic files: output/usha_error.png and output/usha_error.html"
        )

    def _scrape_browser(
        self, category: str, requested_state: str, *, headless: bool
    ) -> List[DealerRecord]:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import Select, WebDriverWait

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

        driver = None
        stage = "starting Chrome"
        try:
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))
            stage = "loading the Usha locator"
            driver.get(self.LOCATOR_URL)
            city_select = wait.until(
                EC.presence_of_element_located((By.ID, "str_city_new"))
            )

            stage = "selecting Bangalore"
            Select(city_select).select_by_visible_text(self.LOCATOR_CITY)
            wait.until(self._results_loaded)
            time.sleep(2)

            records = self._parse_results(
                driver.page_source, category, requested_state
            )
            print(
                f"[Usha] Scraped {len(records)} Bangalore stores "
                "without selecting a store type"
            )
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
    def _results_loaded(driver) -> bool:
        from selenium.webdriver.common.by import By

        count = driver.find_element(By.ID, "store_count").text
        content = driver.find_element(By.ID, "store-list-content")
        return bool(
            re.search(r"Found\s+\d+\s+stores", count, re.IGNORECASE)
            or "No Stores Found" in count
            or content.find_elements(By.CSS_SELECTOR, '[id^="StoreContentLink"]')
        )

    def _parse_results(
        self, html: str, category: str, requested_state: str
    ) -> List[DealerRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records = []
        for card in soup.select('#store-list-content [id^="StoreContentLink"]'):
            body = card.find("div") or card
            strings = list(body.stripped_strings)
            paragraphs = body.find_all("p", recursive=False)
            name = strings[0] if strings else ""
            address = paragraphs[0].get_text(" ", strip=True) if paragraphs else ""
            location = paragraphs[1].get_text(" ", strip=True) if len(paragraphs) > 1 else ""
            phone = ""
            if len(paragraphs) > 2:
                phone = re.sub(
                    r"^Phone:\s*", "", paragraphs[2].get_text(" ", strip=True),
                    flags=re.IGNORECASE,
                )
            pincode = re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", location)
            state = requested_state if requested_state.casefold() in location.casefold() else requested_state

            record = self._make_record(
                category=category,
                state_name=requested_state,
                name=self._normalize(name),
                phone=self._normalize(phone),
                address=self._normalize(address),
                city=self.LOCATOR_CITY,
                state=state,
                pincode=pincode.group(0) if pincode else "",
                dealer_type="Usha Store",
            )
            if record.is_valid():
                records.append(record)
        return records

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "usha_error.png"))
            (output / "usha_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
