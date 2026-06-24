"""Declarative category-to-brand assignments shared by every interface."""

CATEGORY_ALIASES = {
    "Fans": "High Efficient Fans",
    "Fixtures": "Water Efficient Fixtures",
    "Coolroof": "Cool Roof",
}

CATEGORY_BRANDS = {
    "Fans": ["Crompton", "Havells", "Orient", "V-Guard", "Usha", "Kuhl", "GM Modular", "Atomberg"],
    "Fixtures": ["Parryware", "Hindware", "Watertec", "Cera", "Supreme", "Jaquar"],
    "Coolroof": ["Asian Paints", "Dr Fixit", "Berger Paints", "Nerolac"],
}


def canonical_category(category: str) -> str:
    """Return the internal category name expected by brand handlers."""
    match = next(
        (name for name in CATEGORY_ALIASES if name.casefold() == category.casefold()),
        None,
    )
    return CATEGORY_ALIASES.get(match, category)


def brands_for_category(category: str) -> list[str]:
    """Return configured brands for a category, matching case-insensitively."""
    match = next((name for name in CATEGORY_BRANDS if name.casefold() == category.casefold()), None)
    if match is None:
        match = next(
            (
                friendly
                for friendly, canonical in CATEGORY_ALIASES.items()
                if canonical.casefold() == category.casefold()
            ),
            None,
        )
    return list(CATEGORY_BRANDS.get(match, []))
