"""Declarative category-to-brand assignments shared by every interface."""

CATEGORY_BRANDS = {
    "Water Efficient Fixtures": ["Parryware", "Hindware", "Watertec", "Cera", "Supreme", "Jaquar"],
    "High Efficient Fans": ["Crompton", "Havells", "Orient", "V-Guard", "Usha", "Kuhl", "GM Modular", "Atomberg"],
    "Solar Water Heaters": ["V-Guard", "Racold", "Havells"],
    "Solar Panels for Homes": [],
    "Reflective Roof Coating": [],
}


def brands_for_category(category: str) -> list[str]:
    """Return configured brands for a category, matching case-insensitively."""
    match = next((name for name in CATEGORY_BRANDS if name.casefold() == category.casefold()), None)
    return list(CATEGORY_BRANDS.get(match, []))
