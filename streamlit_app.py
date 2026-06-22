"""Web interface for scraping dealers and managing generated S3 files."""

from datetime import datetime
from io import BytesIO
import os
from pathlib import Path

from dotenv import load_dotenv
import openpyxl
import streamlit as st

from catalog import CATEGORY_BRANDS, brands_for_category
from core import PluginRegistry, export_to_xlsx
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
    return str(st.session_state.get("s3_bucket") or os.getenv("BUCKET_NAME", "")).strip()


def render_scraper_dashboard(registry: PluginRegistry) -> None:
    category = st.selectbox("Category", list(CATEGORY_BRANDS))
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

    left, right = st.columns(2)
    state = left.text_input("State", placeholder="Karnataka")
    city = right.text_input("City", placeholder="Bengaluru")
    city_required = [
        brand for brand in selected_brands
        if registry.get(brand) and registry.get(brand).REQUIRES_CITY
    ]
    if city_required:
        st.caption("City is required for: " + ", ".join(city_required))

    bucket_name = active_bucket_name()
    upload_to_s3 = st.checkbox(
        "Upload generated Excel file to S3",
        value=bool(bucket_name),
        disabled=not bucket_name,
        help=(
            f"Uploads privately to s3://{bucket_name}/exports/"
            if bucket_name else "Configure BUCKET_NAME or use the S3 Files tab first."
        ),
    )

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

    progress = st.progress(0, text="Starting scrapers...")
    for index, brand in enumerate(selected_brands, start=1):
        progress.progress((index - 1) / len(selected_brands), text=f"Running {brand}...")
        try:
            handler_class = registry.get(brand)
            if handler_class is None:
                raise LookupError(f"No scraper plugin is installed for {brand}.")
            records.extend(handler_class().fetch(
                category=category, state=state.strip(), city=city.strip()
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
    st.dataframe([record.to_dict() for record in records], use_container_width=True, hide_index=True)
    filename = f"dealers_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    output_path = export_to_xlsx(records, OUTPUT_DIR, filename)
    file_bytes = output_path.read_bytes()
    st.download_button(
        "Download Excel", data=file_bytes, file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    if upload_to_s3:
        try:
            storage = load_storage(bucket_name, os.getenv("AWS_REGION", "us-east-2"))
            key = storage.upload_path(output_path)
            st.success(f"Uploaded privately to s3://{bucket_name}/{key}")
        except Exception as exc:
            st.warning(f"Excel was generated locally, but S3 upload failed: {exc}")


def render_s3_dashboard() -> None:
    st.subheader("S3 file dashboard")
    region = st.text_input("AWS region", value=os.getenv("AWS_REGION", "us-east-2"))
    if "s3_bucket" not in st.session_state:
        st.session_state["s3_bucket"] = os.getenv("BUCKET_NAME", "").strip()
    bucket_name = st.text_input(
        "Private bucket name",
        key="s3_bucket",
        placeholder="globally-unique-dealer-scraper-files",
    ).strip()
    if not bucket_name:
        st.info("Enter a globally unique private bucket name to continue.")
        return

    try:
        storage = load_storage(bucket_name, region.strip())
        exists = storage.bucket_exists()
    except Exception as exc:
        st.error(f"Could not connect to AWS S3: {exc}")
        return

    if not exists:
        st.warning(f"Bucket {bucket_name!r} does not exist or is not available.")
        if st.button("Create private bucket", type="primary"):
            try:
                storage.create_bucket()
                st.success("Bucket created with public access blocked and AES-256 encryption.")
                st.rerun()
            except Exception as exc:
                st.error(f"Bucket creation failed: {exc}")
        return

    upload = st.file_uploader("Upload a file", type=["xlsx", "csv", "json", "txt"])
    if upload is not None and st.button("Upload to S3"):
        try:
            key = storage.upload_fileobj(upload, upload.name)
            st.success(f"Uploaded privately as {key}")
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
            "S3 key": item.key,
            "Size (KB)": round(item.size / 1024, 2),
            "Last modified": item.last_modified,
        }
        for item in files
    ], use_container_width=True, hide_index=True)

    selected_key = st.selectbox("Select a file", [item.key for item in files])
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

    confirm_delete = st.checkbox("I confirm deletion of the selected S3 file")
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
                st.caption("Previewing up to 200 rows")
                st.dataframe(
                    [dict(zip(headers, row)) for row in rows[1:]],
                    use_container_width=True,
                    hide_index=True,
                )
        except Exception as exc:
            st.warning(f"Excel preview unavailable: {exc}")
    elif suffix in {".csv", ".json", ".txt"}:
        st.text_area("Preview", data[:100_000].decode("utf-8", errors="replace"), height=300)
    else:
        st.caption("Preview is unavailable for this file type; use Download instead.")


st.set_page_config(page_title="Dealer Scraper", page_icon="🔎", layout="wide")
st.title("Dealer Scraper")
st.caption("Run brand scrapers and manage generated files in private S3 storage.")

scraper_tab, files_tab = st.tabs(["Run scrapers", "S3 Files"])
with scraper_tab:
    render_scraper_dashboard(load_registry())
with files_tab:
    render_s3_dashboard()
