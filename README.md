# Dealer Scraper

A simple tool for collecting dealer/store data from brand store locators.

The app lets the user choose a category, select one or more brands, enter the
required location details, and download a formatted Excel file.

## Start The App

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the local Streamlit link shown in the terminal, usually:

```text
http://localhost:8501
```

## Categories

Only these categories are shown in the app:

- Fans
- Fixtures
- Coolroof

Some brands need only state and city. Some brands also need pincode. The app
enables the pincode field only when the selected brand needs it.

## Running Scrapers

1. Select a category.
2. Select the brands to scrape.
3. Enter the requested state, city, and pincode.
4. Click `Run scrapers`.
5. Review the table.
6. Click `Download Excel`.

The generated file name uses this format:

```text
city_pincode_category_YYYYMMDD_HHMMSS.xlsx
```

Example:

```text
bengaluru_560038_fans_20260624_122805.xlsx
```

## Output Columns

The Excel file and app table include:

- Duplicate Status
- Source Brand
- Category
- Dealer Name
- Phone
- Email
- Address
- City
- State
- Pincode
- Dealer Type
- Website
- Google Maps
- Latitude
- Longitude

`Website` is only for an actual dealer/company website when one is available.
Google Maps, directions, and locator links are kept out of the Website column.

`Google Maps` contains the directions/maps link when the scraper finds one. If
there is no directions link but latitude and longitude are available, the app
creates a Google Maps link from those coordinates.

## Duplicate Marking

Rows are marked as duplicates when both dealer name and address match another
row.

Duplicates are not removed. They stay visible in the app and Excel file, and
duplicate rows are highlighted in red.

## Saved Files

If shared file storage is configured for this installation, generated Excel
files are also saved automatically in the `Saved files` tab.

From that tab users can:

- See previously generated files
- Preview an Excel file
- Download a selected file
- Upload a file
- Delete a selected file after confirming

Users do not need to enter storage settings in the app.

## Project Files

Important code files and folders:

- `streamlit_app.py` - user interface
- `main.py` - command-line runner
- `catalog.py` - category and brand list
- `brands/` - brand-specific scraper plugins
- `core/` - shared exporter, schema, registry, and storage code
- `requirements.txt` - Python packages

Generated files are written to `output/`. The folder is kept in the project, but
old exports, screenshots, and error HTML files can be deleted safely.
