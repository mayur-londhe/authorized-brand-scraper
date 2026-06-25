#!/usr/bin/env python3
"""
Multi-Brand Dealer Scraper – Orchestration Engine
==================================================
Usage:
    python main.py --brand parryware --category "Water Efficient Fixtures" --state Karnataka --city Bengaluru
    python main.py --brand crompton --category fans --state "Madhya Pradesh" --city Indore
    python main.py --brand all --category "Water Efficient Fixtures" --state Maharashtra --city Mumbai
    python main.py --list-brands
"""

import argparse
import inspect
import sys
from pathlib import Path

# Ensure project root is on the path
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from core import PluginRegistry, export_to_xlsx
from catalog import (
    CATEGORY_BRANDS,
    brands_for_category as get_category_brands,
    canonical_category,
)

OUTPUT_DIR = ROOT / "output"
BRANDS_DIR = ROOT / "brands"


def safe_filename_part(value: str) -> str:
    import re

    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return cleaned or "dealers"


def export_filename(category: str, city: str = "", pincode: str = "") -> str:
    from datetime import datetime

    parts = [
        safe_filename_part(city),
        safe_filename_part(pincode),
        safe_filename_part(category),
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    ]
    return "_".join(part for part in parts if part and part != "dealers") + ".xlsx"

def build_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.discover(BRANDS_DIR)
    return registry


def run_single(
    registry, brand: str, category: str, state: str, city: str = "", pincode: str = ""
):
    handler_cls = registry.get(brand)
    if not handler_cls:
        print(f"[Orchestrator] No plugin found for brand: {brand!r}")
        print(f"               Available: {registry.list_brands()}")
        return []

    handler = handler_cls()
    print(
        f"\n[Orchestrator] Fetching -> Brand={brand!r}  Category={category!r}  "
        f"State={state!r}  City={city!r}  Pincode={pincode!r}"
    )
    kwargs = {"category": category, "state": state, "city": city}
    if "pincode" in inspect.signature(handler.fetch).parameters:
        kwargs["pincode"] = pincode
    records = handler.fetch(**kwargs)
    print(f"[Orchestrator] Got {len(records)} records.")
    return records


def run_all_brands(registry, category: str, state: str, city: str = "", pincode: str = ""):
    """Run all registered brands for a given MGEM category."""
    category_brands = get_category_brands(category)

    if not category_brands:
        print(f"[Orchestrator] No brands configured for category: {category!r}")
        print(f"               MGEM categories: {list(CATEGORY_BRANDS.keys())}")
        return []

    all_records = []
    for brand in category_brands:
        recs = run_single(registry, brand, category, state, city, pincode)
        all_records.extend(recs)

    return all_records


def main():
    parser = argparse.ArgumentParser(
        description="Multi-brand dealer scraper orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --brand parryware --category "Water Efficient Fixtures" --state Karnataka --city Bengaluru
  python main.py --brand crompton --category fans --state "Madhya Pradesh" --city Indore
  python main.py --brand all --category "Water Efficient Fixtures" --state Maharashtra --city Mumbai
  python main.py --list-brands
        """,
    )
    parser.add_argument("--brand",    default="parryware", help='Brand name or "all"')
    parser.add_argument("--category", default="Fixtures", help="Product category")
    parser.add_argument("--state",    default="",         help="State name (required)")
    parser.add_argument("--city",     default="",         help="City name (required for Parryware)")
    parser.add_argument("--pincode",  default="",         help="Pincode (required for Dr Fixit)")
    parser.add_argument("--output",   default="",         help="Custom output filename (.xlsx)")
    parser.add_argument("--list-brands", action="store_true", help="List registered brands and exit")
    args = parser.parse_args()

    registry = build_registry()

    if args.list_brands:
        print("\nRegistered brand plugins:")
        for b in registry.list_brands():
            print(f"  • {b}")
        return

    if not args.state:
        print("[Orchestrator] --state is required.")
        parser.print_help()
        sys.exit(1)

    brand_key = args.brand.lower()
    if brand_key in ("parryware", "all") and not args.city:
        print("[Orchestrator] --city is required for Parryware (and when using --brand all with Water Efficient Fixtures).")
        parser.print_help()
        sys.exit(1)
    category = canonical_category(args.category)

    if (
        brand_key in ("dr fixit", "drfixit", "all")
        and category.casefold() == "cool roof"
        and not args.pincode
    ):
        print("[Orchestrator] --pincode is required for Dr Fixit under Cool Roof.")
        parser.print_help()
        sys.exit(1)

    # ── Dispatch ──────────────────────────────────────────────────────
    if args.brand.lower() == "all":
        records = run_all_brands(
            registry, category, args.state, args.city, args.pincode
        )
    else:
        records = run_single(
            registry, args.brand, category, args.state, args.city, args.pincode
        )

    if not records:
        print("[Orchestrator] No records returned. Nothing to export.")
        return

    # ── Export ────────────────────────────────────────────────────────
    filename = args.output or export_filename(args.category, args.city, args.pincode)
    out_path = export_to_xlsx(records, OUTPUT_DIR, filename=filename)
    print(f"\nDone. Output saved to: {out_path}")


if __name__ == "__main__":
    main()
