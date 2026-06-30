"""Kuhl dealer locator scraper."""

import re
import time
from pathlib import Path
from typing import List

from core.base_handler import BaseBrandHandler
from core.schema import DealerRecord


class KuhlHandler(BaseBrandHandler):
    BRAND_NAME = "Kuhl"
    SUPPORTED_CATEGORIES = ["high efficient fans", "fans"]
    REQUIRES_CITY = False
    REQUIRES_PINCODE = True

    LOCATOR_URLS = (
        "https://www.kuhl.in/where-to-buy",
    )

    PINCODE_RE = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")
    PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")

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
            raise ValueError("Pincode is required for Kuhl.")

        failures = []
        # Keep browser automation hidden and retries inside the page loader.
        for headless in (True,):
            mode = "headless" if headless else "visible"
            try:
                records = self._scrape_browser(
                    category, state, city, pincode, headless=headless
                )
                if records:
                    return records
                failures.append(f"{mode}: 0 rendered shops for pincode")
                print(f"[Kuhl] {failures[-1]}")
            except Exception as exc:
                message = str(exc).splitlines()[0].strip() or "browser timed out"
                failures.append(f"{mode}: {type(exc).__name__}: {message}")
                print(f"[Kuhl] {failures[-1]}")

        raise RuntimeError(
            "Kuhl browser scraper failed. "
            + " | ".join(failures)
            + " Diagnostic files: output/kuhl_error.png and output/kuhl_error.html"
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
        from selenium import webdriver
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
        options.add_experimental_option("useAutomationExtension", False)

        driver = None
        stage = "starting Chrome"
        try:
            print(f"[Kuhl] Opening locator for pincode {pincode}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, max(self.timeout, 35))

            stage = "loading Kuhl locator"
            self._load_locator(driver, wait)
            self._close_popups(driver)

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
            time.sleep(0.3)

            stage = "clicking Kuhl Submit button"
            submit = wait.until(lambda browser: self._submit_button(browser, field))
            old_signature = self._results_signature(driver)
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});"
                "arguments[0].click();",
                submit,
            )

            stage = "waiting for Kuhl shop list"
            wait.until(
                lambda browser: self._results_ready(browser, pincode, old_signature)
            )
            time.sleep(1)

            records = self._parse_results(
                driver.page_source, category, state, city, pincode
            )
            print(f"[Kuhl] Parsed {len(records)} shops")
            return records
        except Exception as exc:
            if driver is not None:
                self._save_diagnostics(driver)
            message = str(exc).splitlines()[0].strip() or "browser timed out"
            raise RuntimeError(f"failed while {stage}: {message}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _load_locator(self, driver, wait) -> None:
        warmup_urls = (
            "https://www.kuhl.in/",
            "https://www.kuhl.in/where-to-buy",
        )
        for url in warmup_urls:
            driver.get(url)
            time.sleep(3)
            if self._pincode_field(driver) and not self._is_server_error(driver):
                return

        for url in self.LOCATOR_URLS:
            for _ in range(3):
                driver.get(url)
                time.sleep(3)
                if self._pincode_field(driver) and not self._is_server_error(driver):
                    return
                driver.refresh()
                time.sleep(2)
        if self._is_server_error(driver):
            raise RuntimeError(
                "Kuhl returned a 500 server error to this browser session. "
                "Try the visible browser again after the page has loaded once manually."
            )
        wait.until(lambda browser: self._pincode_field(browser))

    @staticmethod
    def _is_server_error(driver) -> bool:
        try:
            text = driver.execute_script(
                "return document.body ? document.body.innerText : '';"
            ) or ""
            title = driver.title or ""
        except Exception:
            return False
        combined = f"{title}\n{text}".casefold()
        return "500" in combined and "server error" in combined

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
        result_root = self._result_root(soup, input_pincode) or soup.body or soup
        lines = [
            line.strip()
            for line in result_root.get_text("\n", strip=True).splitlines()
            if line.strip()
        ]

        start = 0
        for index, line in enumerate(lines):
            if re.search(r"search\s+result\s+for", line, re.I):
                start = index + 1
                break

        records = []
        seen = set()
        pending: list[str] = []
        for line in lines[start:]:
            lowered = line.casefold()
            if lowered in {"submit", "or"} or "search by" in lowered:
                continue
            phone_match = re.search(r"Phone:\s*([+\d][\d\s-]{7,})", line, re.I)
            if not phone_match:
                pending.append(line)
                continue

            if len(pending) < 2:
                pending.clear()
                continue
            name = pending[-2]
            address = pending[-1]
            phone = phone_match.group(1).strip()
            record = self._make_shop_record(
                category, state, city, input_pincode, name, address, phone
            )
            pending.clear()
            if record is None:
                continue
            key = (record.name.casefold(), record.address.casefold(), record.phone)
            if key not in seen:
                seen.add(key)
                records.append(record)
        return records

    def _make_shop_record(
        self,
        category: str,
        state: str,
        city: str,
        input_pincode: str,
        name: str,
        address: str,
        phone: str,
    ):
        pincode = self._first_match(self.PINCODE_RE, address) or input_pincode
        record = self._make_record(
            category=category,
            state_name=state,
            name=self._normalize(name),
            phone=self._normalize(phone),
            address=self._normalize(address),
            city=city,
            state=state,
            pincode=pincode,
            dealer_type="Kuhl Dealer",
        )
        return record if record.is_valid() else None

    @staticmethod
    def _result_root(soup, pincode: str):
        marker = soup.find(string=re.compile(rf"Search\s+result\s+for:\s*{re.escape(pincode)}", re.I))
        if marker is None:
            marker = soup.find(string=re.compile(r"Search\s+result\s+for:", re.I))
        node = marker.parent if marker else None
        for _ in range(8):
            if node is None:
                break
            text = node.get_text(" ", strip=True)
            if "Phone:" in text and "Search result for" in text:
                return node
            node = node.parent
        return None

    @staticmethod
    def _pincode_field(driver):
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
              if (text.includes('dealer locator')) score += 20;
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
            "input[placeholder*='pincode']",
            "input[placeholder*='Pincode']",
            "input[name*='pin']",
            "input[id*='pin']",
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
    def _submit_button(driver, field=None):
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
                  candidates.push(...node.querySelectorAll('button, input[type="submit"], input[type="button"], a'));
                }
                return candidates.find((el) => visible(el) && /submit|search|find/.test(label(el))) || null;
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
                    and any(word in label for word in ("submit", "search", "find"))
                ):
                    return element
        return False

    @staticmethod
    def _results_ready(driver, pincode: str, old_signature: tuple[str, ...]):
        signature = KuhlHandler._results_signature(driver)
        if not signature:
            return False
        text = "\n".join(signature)
        if pincode in text and "phone:" in text.casefold():
            return True
        if old_signature and signature != old_signature and "phone:" in text.casefold():
            return True
        return False

    @staticmethod
    def _results_signature(driver) -> tuple[str, ...]:
        try:
            lines = driver.execute_script(
                """
                const text = document.body ? document.body.innerText : '';
                return text.split('\\n').map((line) => line.trim()).filter(Boolean);
                """
            ) or []
        except Exception:
            return ()
        start = 0
        for index, line in enumerate(lines):
            if re.search(r"search\s+result\s+for", line, re.I):
                start = index
                break
        return tuple(lines[start:start + 30])

    @staticmethod
    def _close_popups(driver) -> None:
        from selenium.webdriver.common.by import By

        selectors = (
            "button[aria-label='Close']",
            "button[aria-label='close']",
            ".modal .close",
            ".popup-close",
            "[data-dismiss='modal']",
            "[data-bs-dismiss='modal']",
        )
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    driver.execute_script("arguments[0].click();", element)
                    time.sleep(0.5)

    @staticmethod
    def _first_match(pattern, text: str) -> str:
        match = pattern.search(text or "")
        return match.group(1) if match and match.groups() else match.group(0) if match else ""

    @staticmethod
    def _save_diagnostics(driver) -> None:
        try:
            output = Path(__file__).resolve().parents[1] / "output"
            output.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(output / "kuhl_error.png"))
            (output / "kuhl_error.html").write_text(
                driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass
