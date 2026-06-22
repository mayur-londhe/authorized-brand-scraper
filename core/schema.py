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
    latitude: Optional[str] = None
    longitude: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def is_valid(self) -> bool:
        """Minimum viable record must have a name or phone."""
        return bool(self.name.strip() or self.phone.strip())
