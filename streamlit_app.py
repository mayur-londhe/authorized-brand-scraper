"""Web interface for scraping dealers and managing generated S3 files."""

from datetime import datetime
from io import BytesIO
import inspect
import os
from pathlib import Path
import re
from urllib.parse import quote_plus

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
    records_without_duplicates,
    verify_records_with_google,
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


def google_terms_for_category(category: str) -> str:
    category_key = str(category or "").casefold()
    if "fan" in category_key:
        return "fans"
    if "cool" in category_key or "roof" in category_key or "paint" in category_key:
        return "paint"
    return "bathroom fitting sanitaryware"


def fetch_records(handler, category: str, state: str, city: str, pincode: str = ""):
    kwargs = {"category": category, "state": state, "city": city}
    if "pincode" in inspect.signature(handler.fetch).parameters:
        kwargs["pincode"] = pincode
    return handler.fetch(**kwargs)


BASE_DISPLAY_COLUMNS = {
    "source_brand": "Source Brand",
    "category": "Category",
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
}

GOOGLE_DISPLAY_COLUMNS = {
    "google_name": "Google Name",
    "google_full_address": "Google Full Address",
    "google_contact_number": "Google Contact Number",
    "google_rating": "Google Rating",
    "google_reviews": "Google Reviews",
    "google_business_type": "Google Business Type",
    "google_business_status": "Google Business Status",
    "google_location": "Google Location",
    "google_score": "Google Score",
}

DUPLICATE_STATUS_COLUMN = "Duplicate Status"
HIDDEN_OUTPUT_COLUMNS = {
    "Duplicate Status",
    "State Query",
    "Latitude",
    "Longitude",
    "Google Verified",
    "Google Place ID",
    "Google Get Directions",
    "Google Directions",
}
GOOGLE_OUTPUT_COLUMNS = set(GOOGLE_DISPLAY_COLUMNS.values())


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

    address_column = find_column(dataframe, {"address"})
    if not address_column:
        return dataframe

    keys = []
    counts = {}
    for _, row in dataframe.iterrows():
        address = normalized_text(row.get(address_column, ""))
        key = address if address else None
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
    dataframe = drop_empty_rows(dataframe)
    dataframe = enrich_location_columns(dataframe)
    dataframe = ensure_google_link_columns(dataframe)
    dataframe = drop_empty_google_columns(dataframe)
    dataframe = add_duplicate_status_if_possible(dataframe)
    duplicate_mask = (
        dataframe[DUPLICATE_STATUS_COLUMN].map(is_duplicate_status)
        if DUPLICATE_STATUS_COLUMN in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )
    if DUPLICATE_STATUS_COLUMN in dataframe.columns:
        duplicate_count = duplicate_mask.sum()
        not_duplicate_count = len(dataframe) - duplicate_count
        st.caption(
            f"Not duplicate: {not_duplicate_count} | Duplicate rows: {duplicate_count}"
        )

    def mark_duplicates(row):
        if bool(duplicate_mask.get(row.name, False)):
            return ["background-color: #f4cccc; color: #990000"] * len(row)
        return [""] * len(row)

    if preview_limit is not None:
        st.caption(f"Previewing up to {preview_limit} rows")

    visible_dataframe = dataframe.drop(
        columns=[column for column in HIDDEN_OUTPUT_COLUMNS if column in dataframe.columns]
    )
    column_config = {}
    if "Google Maps" in visible_dataframe.columns:
        column_config["Google Maps"] = st.column_config.LinkColumn(
            "Google Maps",
            display_text="Open Map",
        )
    if "Google Location" in visible_dataframe.columns:
        column_config["Google Location"] = st.column_config.LinkColumn(
            "Google Location",
            display_text="Open Map",
        )
    if "Website" in visible_dataframe.columns:
        column_config["Website"] = st.column_config.LinkColumn("Website")

    link_columns = {"Google Maps", "Google Location", "Website"}
    if link_columns & set(visible_dataframe.columns):
        table = visible_dataframe
    else:
        table = visible_dataframe.style.apply(mark_duplicates, axis=1)

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=column_config or None,
    )


def dataframe_without_duplicate_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    annotated = add_duplicate_status_if_possible(
        drop_empty_rows(drop_empty_google_columns(dataframe))
    )
    if DUPLICATE_STATUS_COLUMN not in annotated.columns:
        return dataframe.drop(
            columns=[column for column in HIDDEN_OUTPUT_COLUMNS if column in dataframe.columns]
        )
    duplicate_statuses = annotated[DUPLICATE_STATUS_COLUMN].astype(str)
    keep_rows = ~duplicate_statuses.str.startswith("Duplicate (same as row")
    deduped = annotated.loc[keep_rows]
    return deduped.drop(
        columns=[column for column in HIDDEN_OUTPUT_COLUMNS if column in deduped.columns]
    )


def dataframe_with_download_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    enriched = enrich_location_columns(dataframe)
    enriched = ensure_google_link_columns(enriched)
    enriched = drop_empty_google_columns(enriched)
    enriched = drop_empty_rows(enriched)
    return enriched.drop(
        columns=[column for column in HIDDEN_OUTPUT_COLUMNS if column in enriched.columns]
    )


def drop_empty_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe
    cleaned = dataframe.replace(r"^\s*$", pd.NA, regex=True)
    return cleaned.dropna(how="all").fillna("")


def ensure_google_link_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    enriched = dataframe.copy()
    address_column = find_column(enriched, {"google full address"})
    name_column = find_column(enriched, {"google name"})
    location_column = find_column(enriched, {"google location"})

    if not address_column and not name_column:
        return enriched

    def destination(row) -> str:
        address = row.get(address_column, "") if address_column else ""
        name = row.get(name_column, "") if name_column else ""
        return str(address or name or "").strip()

    if not location_column:
        enriched["Google Location"] = ""
        location_column = "Google Location"
    enriched[location_column] = enriched.apply(
        lambda row: (
            f"https://www.google.com/maps/search/?api=1&query={quote_plus(destination(row))}"
            if blank_cell(row.get(location_column, "")) and destination(row)
            else row.get(location_column, "")
        ),
        axis=1,
    )

    return enriched


def dataframe_to_xlsx_bytes(dataframe: pd.DataFrame) -> bytes:
    output = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Dealer Logbook"
    for col_index, column in enumerate(dataframe.columns, start=1):
        sheet.cell(row=1, column=col_index, value=str(column))
    for row_index, (_, row) in enumerate(dataframe.iterrows(), start=2):
        for col_index, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=col_index, value="" if pd.isna(value) else value)
    workbook.save(output)
    return output.getvalue()


def saved_file_download_payload(key: str, data: bytes, without_duplicates: bool) -> tuple[bytes, str, str]:
    suffix = Path(key).suffix.casefold()
    name = Path(key).name
    stem = Path(key).stem
    if suffix == ".xlsx":
        workbook = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        if not rows:
            return data, name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        headers = [str(value or "") for value in rows[0]]
        dataframe = pd.DataFrame([dict(zip(headers, row)) for row in rows[1:]])
        clean_dataframe = (
            dataframe_without_duplicate_rows(dataframe)
            if without_duplicates
            else dataframe_with_download_columns(dataframe)
        )
        download_name = f"{stem}_without_duplicates.xlsx" if without_duplicates else name
        return (
            dataframe_to_xlsx_bytes(clean_dataframe),
            download_name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if suffix == ".csv":
        dataframe = pd.read_csv(BytesIO(data))
        clean_dataframe = (
            dataframe_without_duplicate_rows(dataframe)
            if without_duplicates
            else dataframe_with_download_columns(dataframe)
        )
        download_name = f"{stem}_without_duplicates.csv" if without_duplicates else name
        return (
            clean_dataframe.to_csv(index=False).encode("utf-8"),
            download_name,
            "text/csv",
        )
    return data, name, "application/octet-stream"


def drop_empty_google_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    google_columns = [
        column for column in GOOGLE_OUTPUT_COLUMNS
        if column in dataframe.columns
        and dataframe[column].map(lambda value: not blank_cell(value)).sum() == 0
    ]
    if not google_columns:
        return dataframe
    return dataframe.drop(columns=google_columns)


def display_columns_for_records(records) -> dict:
    has_google_data = any(record.google_verified is not None for record in records)
    return (
        {**BASE_DISPLAY_COLUMNS, **GOOGLE_DISPLAY_COLUMNS}
        if has_google_data
        else BASE_DISPLAY_COLUMNS
    )


def render_records_table(records) -> None:
    rows = records_with_duplicate_status(records)
    dataframe = pd.DataFrame(rows)
    display_columns = display_columns_for_records(records)
    dataframe = dataframe[[column for column in display_columns if column in dataframe.columns]]
    dataframe = dataframe.rename(columns=display_columns)
    render_duplicate_dataframe(dataframe)


def records_download_dataframe(records) -> pd.DataFrame:
    rows = records_with_duplicate_status(records)
    dataframe = pd.DataFrame(rows)
    display_columns = display_columns_for_records(records)
    dataframe = dataframe[[column for column in display_columns if column in dataframe.columns]]
    dataframe = dataframe.rename(columns=display_columns)
    return enrich_location_columns(dataframe)


def save_records_to_shared_files(
    records,
    *,
    filename: str,
    bucket_name: str,
) -> tuple[Path, str | None, str | None]:
    output_path = export_to_xlsx(records, OUTPUT_DIR, filename)
    if not bucket_name:
        return output_path, None, None
    try:
        storage = load_storage(bucket_name, active_region())
        key = storage.upload_path(output_path, key=f"exports/{filename}")
        return output_path, key, None
    except Exception as exc:
        return output_path, None, str(exc)


def render_scraper_dashboard(registry: PluginRegistry) -> None:
    category = st.selectbox("Category", list(CATEGORY_BRANDS))
    scraper_category = canonical_category(category)
    brands = available_brands(registry, category)
    configured = brands_for_category(category)
    missing = [name for name in configured if registry.get(name) is None]

    if not brands:
        st.warning("No sources are available for this category yet.")
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

    scrape_signature = {
        "category": category,
        "scraper_category": scraper_category,
        "brands": tuple(selected_brands),
        "state": state.strip(),
        "city": city.strip(),
        "pincode": pincode.strip(),
    }

    if st.button("Run", type="primary", use_container_width=True):
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

        progress = st.progress(0, text="Starting...")
        for index, brand in enumerate(selected_brands, start=1):
            progress.progress((index - 1) / len(selected_brands), text=f"Running {brand}...")
            try:
                handler_class = registry.get(brand)
                if handler_class is None:
                    raise LookupError(f"No source is installed for {brand}.")
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
        raw_count = len(records)
        records = records_without_duplicates(records)
        filename = export_filename(category, city.strip(), pincode.strip())
        _, saved_key, save_error = save_records_to_shared_files(
            records,
            filename=filename,
            bucket_name=bucket_name,
        )

        st.session_state.pop("google_verified_result", None)
        st.session_state["scraper_result"] = {
            "signature": scrape_signature,
            "records": records,
            "errors": errors,
            "filename": filename,
            "raw_count": raw_count,
            "saved_key": saved_key,
            "save_error": save_error,
        }

    result = st.session_state.get("scraper_result")
    if not result or result.get("signature") != scrape_signature:
        return

    records = result.get("records", [])
    errors = result.get("errors", [])
    if errors:
        st.warning("Some sources could not finish:\n\n" + "\n\n".join(
            f"- {error}" for error in errors
        ))
    if not records:
        if not errors:
            st.info("No dealer records were returned.")
        return

    st.success(f"Found {len(records)} dealer records across {len(selected_brands)} brand(s).")
    if result.get("raw_count", len(records)) != len(records):
        st.caption(
            f"Removed {result.get('raw_count', len(records)) - len(records)} duplicate address row(s)."
        )
    if result.get("saved_key"):
        st.caption(f"Saved to shared files: {Path(result['saved_key']).name}")
    elif result.get("save_error"):
        st.warning(f"Local Excel was generated, but shared file save failed: {result['save_error']}")
    render_records_table(records)

    st.divider()
    if st.button(
        "Verify with Google Places",
        type="primary",
        use_container_width=True,
        help=(
            "Keeps only Google operational businesses with a phone number, "
            "then sorts by Google rating and review count."
        ),
    ):
        before_count = len(records)
        verify_progress = st.progress(0, text="Starting Google Places verification...")

        def update_google_progress(index: int, total: int, record) -> None:
            name = str(getattr(record, "name", "") or "dealer")
            verify_progress.progress(index / total, text=f"Checking {name}...")

        try:
            verified_records = verify_records_with_google(
                records,
                state=state.strip(),
                city=city.strip(),
                pincode=pincode.strip(),
                search_terms=google_terms_for_category(category),
                on_progress=update_google_progress,
            )
        except Exception as exc:
            verify_progress.empty()
            st.error(f"Google Places verification failed: {exc}")
            return
        verify_progress.empty()
        _, verified_saved_key, verified_save_error = save_records_to_shared_files(
            verified_records,
            filename=result["filename"],
            bucket_name=bucket_name,
        )
        st.session_state["google_verified_result"] = {
            "signature": scrape_signature,
            "records": verified_records,
            "source_count": before_count,
            "filename": result["filename"],
            "saved_key": verified_saved_key,
            "save_error": verified_save_error,
        }

    verified_result = st.session_state.get("google_verified_result")
    if not verified_result or verified_result.get("signature") != scrape_signature:
        return

    verified_records = verified_result.get("records", [])
    st.info(
        f"Google Places kept {len(verified_records)} of "
        f"{verified_result.get('source_count', len(records))} records "
        "with operational status and phone numbers."
    )
    if not verified_records:
        return

    render_records_table(verified_records)
    if verified_result.get("saved_key"):
        st.caption(f"Replaced saved file: {Path(verified_result['saved_key']).name}")
    elif verified_result.get("save_error"):
        st.warning(f"Verified Excel was generated, but shared file save failed: {verified_result['save_error']}")


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
        without_duplicates = st.checkbox(
            "Download without duplicate rows",
            key=f"without-duplicates-{selected_key}",
        )
        try:
            download_data, download_name, download_mime = saved_file_download_payload(
                selected_key,
                data,
                without_duplicates,
            )
        except Exception as exc:
            st.warning(f"Could not prepare download: {exc}")
            download_data = data
            download_name = Path(selected_key).name
            download_mime = "application/octet-stream"
        st.download_button(
            "Download file",
            data=download_data,
            file_name=download_name,
            mime=download_mime,
            key=f"download-selected-{selected_key}",
            use_container_width=True,
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
            workbook = openpyxl.load_workbook(BytesIO(data), read_only=False, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(max_row=201))
            if rows:
                headers = [str(cell.value or "") for cell in rows[0]]
                preview_rows = []
                for row in rows[1:]:
                    values = [
                        cell.hyperlink.target
                        if cell.hyperlink and cell.hyperlink.target
                        else cell.value
                        for cell in row
                    ]
                    preview_rows.append(dict(zip(headers, values)))
                dataframe = pd.DataFrame(preview_rows)
                render_duplicate_dataframe(dataframe, preview_limit=200)
        except Exception as exc:
            st.warning(f"Excel preview unavailable: {exc}")
    elif suffix == ".csv":
        try:
            dataframe = pd.read_csv(BytesIO(data))
            render_duplicate_dataframe(dataframe, preview_limit=200)
        except Exception as exc:
            st.warning(f"CSV preview unavailable: {exc}")
    elif suffix in {".json", ".txt"}:
        st.text_area("Preview", data[:100_000].decode("utf-8", errors="replace"), height=300)
    else:
        st.caption("Preview is unavailable for this file type; use Download instead.")


st.set_page_config(page_title="Dealer Finder", page_icon="🔎", layout="wide")
st.markdown(
    """
    <style>
    .stButton button[kind="primary"] {
        background-color: #16803c;
        border-color: #16803c;
        color: #ffffff;
    }
    .stButton button[kind="primary"]:hover {
        background-color: #0f6b31;
        border-color: #0f6b31;
        color: #ffffff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Dealer Finder")
st.caption("Run brand sources and manage generated Excel files.")

scraper_tab, files_tab = st.tabs(["Run", "Saved files"])
with scraper_tab:
    render_scraper_dashboard(load_registry())
with files_tab:
    render_s3_dashboard()
