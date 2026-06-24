"""Web interface for scraping dealers and managing generated S3 files."""

from datetime import datetime
from io import BytesIO
import inspect
import os
from pathlib import Path
import re

from dotenv import load_dotenv
import openpyxl
import pandas as pd
import streamlit as st

from catalog import CATEGORY_BRANDS, brands_for_category, canonical_category
from core import (
    PluginRegistry,
    export_to_xlsx,
    google_maps_url,
    pincode_from_address,
    records_with_duplicate_status,
)
from core.s3_storage import S3Storage

ROOT = Path(__file__).parent.resolve()
OUTPUT_DIR = ROOT / "output"
load_dotenv(ROOT / ".env")


@st.cache_resource
def load_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.discover(ROOT / "brands")
    return registry


@st.cache_resource
def load_storage(bucket_name: str, region: str) -> S3Storage:
    return S3Storage(bucket_name=bucket_name, region=region)


def available_brands(registry: PluginRegistry, category: str) -> list[str]:
    installed = {name.casefold(): name for name in registry.list_brands()}
    return [
        installed[name.casefold()]
        for name in brands_for_category(category)
        if name.casefold() in installed
    ]


def active_bucket_name() -> str:
    return str(os.getenv("BUCKET_NAME", "")).strip()


def active_region() -> str:
    return str(os.getenv("AWS_REGION", "us-east-2")).strip()


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return cleaned or "dealers"


def export_filename(category: str, city: str = "", pincode: str = "") -> str:
    parts = [
        safe_filename_part(city),
        safe_filename_part(pincode),
        safe_filename_part(category),
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    ]
    return "_".join(part for part in parts if part and part != "dealers") + ".xlsx"


def fetch_records(handler, category: str, state: str, city: str, pincode: str = ""):
    kwargs = {"category": category, "state": state, "city": city}
    if "pincode" in inspect.signature(handler.fetch).parameters:
        kwargs["pincode"] = pincode
    return handler.fetch(**kwargs)


DISPLAY_COLUMNS = {
    "duplicate_status": "Duplicate Status",
    "source_brand": "Source Brand",
    "category": "Category",
    "state_name": "State Query",
    "name": "Dealer Name",
    "phone": "Phone",
    "email": "Email",
    "address": "Address",
    "city": "City",
    "state": "State",
    "pincode": "Pincode",
    "dealer_type": "Dealer Type",
    "website": "Website",
    "map_url": "Google Maps",
    "latitude": "Latitude",
    "longitude": "Longitude",
}

DUPLICATE_STATUS_COLUMN = "Duplicate Status"


def is_duplicate_status(value) -> bool:
    return str(value or "").startswith("Duplicate")


def normalized_text(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def find_column(dataframe: pd.DataFrame, options: set[str]) -> str | None:
    for column in dataframe.columns:
        if str(column).strip().casefold() in options:
            return column
    return None


def blank_cell(value) -> bool:
    return value is None or pd.isna(value) or not str(value).strip()


def looks_like_map_url(value) -> bool:
    text = str(value or "").casefold()
    return "google.com/maps" in text or "maps.google" in text or "maps?q=" in text


def enrich_location_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    enriched = dataframe.copy()
    address_column = find_column(enriched, {"address"})
    pincode_column = find_column(enriched, {"pincode", "pin code", "postal code"})

    if address_column:
        if not pincode_column:
            enriched["Pincode"] = ""
            pincode_column = "Pincode"
        enriched[pincode_column] = enriched.apply(
            lambda row: pincode_from_address(row.get(address_column, ""))
            if blank_cell(row.get(pincode_column, ""))
            else row.get(pincode_column, ""),
            axis=1,
        )

    lat_column = find_column(enriched, {"latitude", "lat"})
    lon_column = find_column(enriched, {"longitude", "long", "lng", "lon"})
    maps_column = find_column(enriched, {"google maps", "map url", "map_url", "maps"})
    website_column = find_column(enriched, {"website"})
    if not maps_column:
        enriched["Google Maps"] = ""
        maps_column = "Google Maps"

    if website_column:
        enriched[maps_column] = enriched.apply(
            lambda row: row.get(website_column, "")
            if blank_cell(row.get(maps_column, "")) and looks_like_map_url(row.get(website_column, ""))
            else row.get(maps_column, ""),
            axis=1,
        )
        enriched[website_column] = enriched[website_column].apply(
            lambda value: "" if looks_like_map_url(value) else value
        )

    if lat_column and lon_column:
        if not maps_column:
            enriched["Google Maps"] = ""
            maps_column = "Google Maps"
        enriched[maps_column] = enriched.apply(
            lambda row: google_maps_url(row.get(lat_column, ""), row.get(lon_column, ""))
            if blank_cell(row.get(maps_column, ""))
            else row.get(maps_column, ""),
            axis=1,
        )

    return enriched


def add_duplicate_status_if_possible(dataframe: pd.DataFrame) -> pd.DataFrame:
    if DUPLICATE_STATUS_COLUMN in dataframe.columns:
        return dataframe

    name_column = find_column(dataframe, {"dealer name", "name"})
    address_column = find_column(dataframe, {"address"})
    if not name_column or not address_column:
        return dataframe

    keys = []
    counts = {}
    for _, row in dataframe.iterrows():
        name = normalized_text(row.get(name_column, ""))
        address = normalized_text(row.get(address_column, ""))
        key = (name, address) if name and address else None
        keys.append(key)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1

    first_rows = {}
    statuses = []
    for sheet_row, key in enumerate(keys, start=2):
        if key is None or counts.get(key, 0) < 2:
            statuses.append("Not Duplicate")
            continue

        first_row = first_rows.setdefault(key, sheet_row)
        if first_row == sheet_row:
            statuses.append(f"Duplicate (first record, {counts[key]} matches)")
        else:
            statuses.append(f"Duplicate (same as row {first_row})")

    annotated = dataframe.copy()
    annotated.insert(0, DUPLICATE_STATUS_COLUMN, statuses)
    return annotated


def render_duplicate_dataframe(dataframe: pd.DataFrame, preview_limit: int | None = None) -> None:
    dataframe = enrich_location_columns(dataframe)
    dataframe = add_duplicate_status_if_possible(dataframe)
    if DUPLICATE_STATUS_COLUMN in dataframe.columns:
        duplicate_count = dataframe[DUPLICATE_STATUS_COLUMN].map(is_duplicate_status).sum()
        not_duplicate_count = len(dataframe) - duplicate_count
        st.caption(
            f"Not duplicate: {not_duplicate_count} | Duplicate rows: {duplicate_count}"
        )

    def mark_duplicates(row):
        if is_duplicate_status(row.get(DUPLICATE_STATUS_COLUMN, "")):
            return ["background-color: #f4cccc; color: #990000"] * len(row)
        return [""] * len(row)

    if preview_limit is not None:
        st.caption(f"Previewing up to {preview_limit} rows")

    table = (
        dataframe.style.apply(mark_duplicates, axis=1)
        if DUPLICATE_STATUS_COLUMN in dataframe.columns
        else dataframe
    )
    column_config = {}
    if "Google Maps" in dataframe.columns:
        column_config["Google Maps"] = st.column_config.LinkColumn(
            "Google Maps",
            display_text="Open Map",
        )
    if "Website" in dataframe.columns:
        column_config["Website"] = st.column_config.LinkColumn("Website")

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=column_config or None,
    )


def render_records_table(records) -> None:
    rows = records_with_duplicate_status(records)
    dataframe = pd.DataFrame(rows)
    dataframe = dataframe[[column for column in DISPLAY_COLUMNS if column in dataframe.columns]]
    dataframe = dataframe.rename(columns=DISPLAY_COLUMNS)
    render_duplicate_dataframe(dataframe)


def render_scraper_dashboard(registry: PluginRegistry) -> None:
    category = st.selectbox("Category", list(CATEGORY_BRANDS))
    scraper_category = canonical_category(category)
    brands = available_brands(registry, category)
    configured = brands_for_category(category)
    missing = [name for name in configured if registry.get(name) is None]

    if not brands:
        st.warning("No scraper plugins are available for this category yet.")
        if missing:
            st.caption("Configured, but not implemented: " + ", ".join(missing))
        return

    selected_brands = st.multiselect("Brands", brands, default=brands)
    if missing:
        st.caption("Coming later (no plugin installed): " + ", ".join(missing))

    left, middle, right = st.columns(3)
    state = left.text_input("State", placeholder="Karnataka")
    city = middle.text_input("City", placeholder="Bengaluru")
    pincode_required = [
        brand for brand in selected_brands
        if registry.get(brand) and getattr(registry.get(brand), "REQUIRES_PINCODE", False)
    ]
    pincode = right.text_input(
        "Pincode",
        placeholder="560001",
        disabled=not pincode_required,
    )
    city_required = [
        brand for brand in selected_brands
        if registry.get(brand) and registry.get(brand).REQUIRES_CITY
    ]
    if city_required:
        st.caption("City is required for: " + ", ".join(city_required))
    if pincode_required:
        st.caption("Pincode is required for: " + ", ".join(pincode_required))

    bucket_name = active_bucket_name()
    upload_to_s3 = bool(bucket_name)

    if not st.button("Run scrapers", type="primary", use_container_width=True):
        return

    errors: list[str] = []
    records = []
    if not selected_brands:
        st.error("Select at least one brand.")
        return
    if not state.strip():
        st.error("State is required.")
        return
    if city_required and not city.strip():
        st.error("City is required for: " + ", ".join(city_required))
        return
    if pincode_required and not pincode.strip():
        st.error("Pincode is required for: " + ", ".join(pincode_required))
        return

    progress = st.progress(0, text="Starting scrapers...")
    for index, brand in enumerate(selected_brands, start=1):
        progress.progress((index - 1) / len(selected_brands), text=f"Running {brand}...")
        try:
            handler_class = registry.get(brand)
            if handler_class is None:
                raise LookupError(f"No scraper plugin is installed for {brand}.")
            records.extend(fetch_records(
                handler_class(),
                category=scraper_category,
                state=state.strip(),
                city=city.strip(),
                pincode=pincode.strip(),
            ))
        except Exception as exc:
            errors.append(f"{brand}: {exc}")
        progress.progress(index / len(selected_brands), text=f"Finished {brand}")
    progress.empty()

    if errors:
        st.warning("Some scrapers could not finish:\n\n" + "\n\n".join(
            f"- {error}" for error in errors
        ))
    if not records:
        if not errors:
            st.info("The scrapers completed, but no dealer records were returned.")
        return

    st.success(f"Found {len(records)} dealer records across {len(selected_brands)} brand(s).")
    render_records_table(records)
    filename = export_filename(category, city.strip(), pincode.strip())
    output_path = export_to_xlsx(records, OUTPUT_DIR, filename)
    file_bytes = output_path.read_bytes()
    st.download_button(
        "Download Excel", data=file_bytes, file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    if upload_to_s3:
        try:
            storage = load_storage(bucket_name, active_region())
            key = storage.upload_path(output_path)
            st.success("Saved to shared files.")
        except Exception as exc:
            st.warning(f"Excel was generated locally, but shared file upload failed: {exc}")


def render_s3_dashboard() -> None:
    st.subheader("Saved files")
    region = active_region()
    bucket_name = active_bucket_name()
    if not bucket_name:
        st.info("Shared file storage is not configured yet.")
        return

    try:
        storage = load_storage(bucket_name, region)
        exists = storage.bucket_exists()
    except Exception as exc:
        st.error(f"Could not connect to shared file storage: {exc}")
        return

    if not exists:
        st.warning("Shared file storage is not available.")
        return

    upload = st.file_uploader("Upload a file", type=["xlsx", "csv", "json", "txt"])
    if upload is not None and st.button("Upload file"):
        try:
            key = storage.upload_fileobj(upload, upload.name)
            st.success(f"Uploaded {Path(key).name}")
            st.rerun()
        except Exception as exc:
            st.error(f"Upload failed: {exc}")

    try:
        files = storage.list_files()
    except Exception as exc:
        st.error(f"Could not list files: {exc}")
        return
    if not files:
        st.info("The exports/ folder is empty.")
        return

    st.dataframe([
        {
            "File": item.name,
            "Size (KB)": round(item.size / 1024, 2),
            "Last modified": item.last_modified,
        }
        for item in files
    ], use_container_width=True, hide_index=True)

    file_options = {item.name: item.key for item in files}
    selected_name = st.selectbox("Select a file", list(file_options))
    selected_key = file_options[selected_name]
    if st.button("Retrieve and preview"):
        try:
            st.session_state["s3_selected_data"] = storage.download_bytes(selected_key)
            st.session_state["s3_selected_key"] = selected_key
        except Exception as exc:
            st.error(f"Retrieval failed: {exc}")

    if st.session_state.get("s3_selected_key") == selected_key:
        data = st.session_state.get("s3_selected_data", b"")
        st.download_button(
            "Download selected file", data=data,
            file_name=Path(selected_key).name,
            mime="application/octet-stream",
        )
        render_file_preview(selected_key, data)

    confirm_delete = st.checkbox("I confirm deletion of the selected file")
    if st.button("Delete selected file", disabled=not confirm_delete):
        try:
            storage.delete_file(selected_key)
            st.session_state.pop("s3_selected_data", None)
            st.session_state.pop("s3_selected_key", None)
            st.success(f"Deleted {selected_key}")
            st.rerun()
        except Exception as exc:
            st.error(f"Deletion failed: {exc}")


def render_file_preview(key: str, data: bytes) -> None:
    suffix = Path(key).suffix.casefold()
    if suffix == ".xlsx":
        try:
            workbook = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True, max_row=201))
            if rows:
                headers = [str(value or "") for value in rows[0]]
                dataframe = pd.DataFrame([dict(zip(headers, row)) for row in rows[1:]])
                render_duplicate_dataframe(dataframe, preview_limit=200)
        except Exception as exc:
            st.warning(f"Excel preview unavailable: {exc}")
    elif suffix in {".csv", ".json", ".txt"}:
        st.text_area("Preview", data[:100_000].decode("utf-8", errors="replace"), height=300)
    else:
        st.caption("Preview is unavailable for this file type; use Download instead.")


st.set_page_config(page_title="Dealer Scraper", page_icon="🔎", layout="wide")
st.title("Dealer Scraper")
st.caption("Run brand scrapers and manage generated Excel files.")

scraper_tab, files_tab = st.tabs(["Run scrapers", "Saved files"])
with scraper_tab:
    render_scraper_dashboard(load_registry())
with files_tab:
    render_s3_dashboard()
