import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class HavellsHandler(BaseBrandHandler):
    BRAND_NAME = "Havells"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = True

    LOCATOR_URL = "https://havells.com/store-locator"
    PRODUCT_CATEGORY = "Fans"
    
    # Store types
    DEALER_TYPE = "dealer"
    EXCLUSIVE_STORE_TYPE = "Exclusive Brand Stores"

    BANGALORE_DEALER_CITIES = (
        "BANGALORE-KA",
        "BANGALORE NORTH-KA",
        "BANGALORE SOUTH-KA",
    )
    BANGALORE_EXCLUSIVE_CITIES = ("BANGALORE-KA",)

    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        state = self._normalize(state)
        city = self._normalize(city)
        if not state or not city:
            raise ValueError("State and city are required for Havells.")
        
        records = []
        seen = set()
        failed_attempts = []

        # We want to pull from both Dealer and Exclusive Brand Stores types
        target_types = [self.DEALER_TYPE, self.EXCLUSIVE_STORE_TYPE]

        for store_type in target_types:
            # Determine appropriate city array for Bangalore based on store type
            if city.casefold() in ("bengaluru", "bangalore"):
                locator_cities = (
                    self.BANGALORE_DEALER_CITIES 
                    if store_type == self.DEALER_TYPE 
                    else self.BANGALORE_EXCLUSIVE_CITIES
                )
            else:
                locator_cities = (city,)

            for locator_city in locator_cities:
                try:
                    city_records = self._fetch_locator_city(
                        category, state, locator_city, store_type
                    )
                except RuntimeError as exc:
                    failed_attempts.append(f"{store_type} -> {locator_city}: {exc}")
                    print(f"[Havells] Continuing after failure for {store_type} in {locator_city}")
                    continue

                for record in city_records:
                    key = (
                        record.name.casefold(),
                        record.phone,
                        record.address.casefold(),
                    )
                    if key not in seen:
                        seen.add(key)
                        records.append(record)

        if not records and failed_attempts:
            raise RuntimeError(" | ".join(failed_attempts))

        print(f"[Havells] Combined {len(records)} unique records across requested target types.")
        if failed_attempts:
            print("[Havells] Some locator searches failed: " + " | ".join(failed_attempts))
            
        return records

    def _fetch_locator_city(
        self, category: str, state: str, locator_city: str, store_type: str
    ) -> List[DealerRecord]:
        failures = []
        for headless in (True, False):
            try:
                return self._scrape_browser(
                    category, state, locator_city, store_type, headless=headless
                )
            except Exception as exc:
                mode = "headless" if headless else "visible"
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Havells] {locator_city} ({store_type}) — {failures[-1]}")

        raise RuntimeError(
            f"Havells scraper failed for {locator_city} [{store_type}] after headless and visible attempts. "
            + " | ".join(failures)
            + " Diagnostic files: output/havells_error.png and output/havells_error.html"
        )

    def _scrape_browser(
        self, category: str, state: str, city: str, store_type: str, *, headless: bool
    ) -> List[DealerRecord]:
        from bs4 import BeautifulSoup
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

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
            mode = "headless" if headless else "visible"
            print(f"[Havells] Opening {mode} locator for {city}, {state} [{store_type}]")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))
            
            stage = "loading the Havells locator"
            driver.get(self.LOCATOR_URL)
            wait.until(EC.presence_of_element_located((By.ID, "selectType")))
            time.sleep(3)

            stage = f"selecting locator type: {store_type}"
            self._select_value(driver, wait, "selectType", store_type)

            stage = f"selecting state '{state}'"
            self._select_value(driver, wait, "selectState", state)

            stage = f"selecting city '{city}'"
            self._select_value(driver, wait, "selectCity", city)

            # Only select product category if it's NOT an Exclusive Brand Store
            if store_type != self.EXCLUSIVE_STORE_TYPE:
                stage = "selecting Fans product category"
                try:
                    self._select_value(
                        driver,
                        WebDriverWait(driver, 10),
                        "selectCategory",
                        self.PRODUCT_CATEGORY,
                        inject_if_missing=False,  # Do not inject; cleanly fail if missing
                    )
                except Exception as cat_exc:
                    print(f"[Havells] Skipping category selection block: {cat_exc}")

            initial_signature = self._result_signature(driver)
            stage = "submitting the Havells dealer search"
            submit = wait.until(
                EC.presence_of_element_located(
                    (By.ID, "store_locator_filter_submit")
                )
            )
            driver.execute_script(
                "arguments[0].click();"
                "if (window.jQuery) { jQuery(arguments[0]).trigger('click'); }",
                submit,
            )
            wait.until(
                lambda browser: self._results_ready(browser, initial_signature)
            )
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select("#store_locator_list > div")
            records = [
                record
                for card in cards
                if (record := self._parse_card(card, category, state, city, store_type))
                is not None
            ]
            print(f"[Havells] Parsed {len(records)} dealer cards")
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
    def _select_value(
        driver,
        wait,
        select_id: str,
        requested: str,
        *,
        inject_if_missing: bool = False,
    ) -> None:
        """Wait for an AJAX-populated option, set it, and dispatch change."""
        from selenium.webdriver.common.by import By

        def matching_option(browser):
            requested_norm = HavellsHandler._norm(requested)
            for option in browser.find_elements(
                By.CSS_SELECTOR, f"#store_locator_filter #{select_id} option"
            ):
                value = (option.get_attribute("value") or "").strip()
                label = (option.get_attribute("textContent") or "").strip()
                value_norm = HavellsHandler._norm(value)
                label_norm = HavellsHandler._norm(label)
                if HavellsHandler._option_matches(
                    requested_norm, value_norm, label_norm
                ):
                    return option
            return False

        try:
            option = wait.until(matching_option)
        except Exception:
            if not inject_if_missing:
                raise
            driver.execute_script(
                "const option=document.createElement('option');"
                "option.value=arguments[1]; option.text=arguments[1];"
                "arguments[0].appendChild(option);",
                wait.until(lambda browser: HavellsHandler._filter_select(browser, select_id)),
                requested,
            )
            option = matching_option(driver)
            if not option:
                raise RuntimeError(
                    f"Could not add {requested!r} to #{select_id}"
                )
        value = option.get_attribute("value")
        select = wait.until(lambda browser: HavellsHandler._filter_select(browser, select_id))
        driver.execute_script(
            "arguments[0].value=arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
            select,
            value,
        )
        driver.execute_script(
            "if (window.jQuery) {"
            "  jQuery(arguments[0]).val(arguments[1]).trigger('input').trigger('change');"
            "}",
            select,
            value,
        )
        driver.execute_script(
            "arguments[0].value=arguments[1];"
            "if (window.jQuery) { jQuery(arguments[0]).val(arguments[1]); }",
            select,
            value,
        )

    @staticmethod
    def _filter_select(driver, select_id: str):
        from selenium.webdriver.common.by import By

        for select in driver.find_elements(
            By.CSS_SELECTOR, f"#store_locator_filter #{select_id}"
        ):
            if select.is_enabled():
                return select
        return False

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
    def _result_signature(driver):
        from selenium.webdriver.common.by import By

        return tuple(
            card.text[:250]
            for card in driver.find_elements(
                By.CSS_SELECTOR, "#store_locator_list > div"
            )[:3]
        )

    @classmethod
    def _results_ready(cls, driver, initial_signature):
        signature = cls._result_signature(driver)
        if signature and signature != initial_signature:
            return signature
        try:
            text = driver.execute_script(
                """
                const root = document.querySelector('#store_locator_list');
                return root ? (root.innerText || root.textContent || '').trim() : '';
                """
            ) or ""
        except Exception:
            text = ""
        if text and "no record found" in text.casefold():
            return text
        return False

    def _parse_card(self, card, category: str, state: str, city: str, store_type: str):
        name = card.select_one("h5")
        address = card.select_one("address")
        if name is None or address is None:
            return None

        lines = [line.strip() for line in address.get_text("\n").splitlines() if line.strip()]
        text = "\n".join(lines)
        phone = self._field(text, r"Tel:\s*(.+)")
        email = self._field(text, r"Email:\s*(\S+)")
        pincode = self._field(text, r"Postal Code:\s*([^\n]+)")
        result_city = self._field(text, r"City:\s*([^\n]+)") or city
        address_lines = [
            line
            for line in lines
            if not re.match(
                r"^(District|City|Postal Code|Tel|Email):", line, re.I
            )
        ]
        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name.get_text(" ", strip=True)),
            phone=self._normalize(phone),
            email=self._normalize(email),
            address=self._normalize(", ".join(address_lines)),
            city=self._normalize(result_city),
            state=state,
            pincode=self._normalize(pincode),
            dealer_type=store_type,
        )
        return record if record.is_valid() else None

    @staticmethod
    def _field(text: str, pattern: str) -> str:
        match = re.search(pattern, text, re.I)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "havells_error.png"))
            (output / "havells_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
