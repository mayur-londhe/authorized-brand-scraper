"""
Abstract base class every brand plugin must implement.
"""
from abc import ABC, abstractmethod
from typing import List
from .schema import DealerRecord


class BaseBrandHandler(ABC):
    """
    One subclass per brand. Drop the file in /brands/ and the
    orchestrator auto-discovers it via the plugin registry.
    """

    # ── Override these two class-level attributes in every plugin ──
    BRAND_NAME: str = ""          # e.g. "Crompton"
    SUPPORTED_CATEGORIES: list = []  # e.g. ["fans", "water heaters"]
    REQUIRES_CITY: bool = False
    REQUIRES_PINCODE: bool = False

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "en-IN,en;q=0.9",
        }

    # ── Core method every plugin must implement ──────────────────────
    @abstractmethod
    def fetch(self, category: str, state: str, city: str = "") -> List[DealerRecord]:
        """
        Fetch dealer records for the given category + state (+ optional city).
        Prefer hitting the brand's JSON/XHR API endpoint.
        Fall back to HTML scraping only when no API is available.
        Return a list of DealerRecord objects.
        """
        ...

    # ── Shared helpers available to all plugins ───────────────────────
    def _normalize(self, value: str) -> str:
        """Strip and title-case a string field."""
        return (value or "").strip()

    def _make_record(self, **kwargs) -> DealerRecord:
        """
        Convenience factory – passes brand/category/state automatically.
        Usage inside fetch():
            record = self._make_record(name="ABC Electricals", phone="9876543210")
        """
        return DealerRecord(
            source_brand=self.BRAND_NAME,
            **kwargs,
        )

    def __repr__(self):
        return f"<{self.__class__.__name__} brand={self.BRAND_NAME!r}>"
