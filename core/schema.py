"""
Uniform dealer/distributor record schema.
Every brand handler must map its raw data to this structure.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DealerRecord:
    # --- Orchestration metadata ---
    source_brand: str = ""       # e.g. "Crompton"
    category: str = ""           # e.g. "fans"
    state_name: str = ""         # e.g. "Madhya Pradesh"

    # --- Dealer identity ---
    name: str = ""
    phone: str = ""
    email: str = ""

    # --- Location ---
    address: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""

    # --- Optional extras (drop or extend per brand) ---
    dealer_type: Optional[str] = None   # "Authorised Distributor", "Retailer", etc.
    website: Optional[str] = None
    map_url: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None

    # --- Google Places verification ---
    google_verified: Optional[bool] = None
    google_name: Optional[str] = None
    google_full_address: Optional[str] = None
    google_contact_number: Optional[str] = None
    google_rating: Optional[float] = None
    google_reviews: Optional[int] = None
    google_business_type: Optional[str] = None
    google_business_status: Optional[str] = None
    google_place_id: Optional[str] = None
    google_location: Optional[str] = None
    google_name_match_score: Optional[float] = None
    google_verification_status: Optional[str] = None
    google_verification_reason: Optional[str] = None
    google_score: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def is_valid(self) -> bool:
        """Minimum viable record must have a name or phone."""
        return bool(self.name.strip() or self.phone.strip())
