# Dealer Finder

A simple tool for collecting dealer/store data from brand store locators.

The app lets the user choose a category, select one or more brands, enter the
required location details, verify results with Google Places, and manage saved
Excel files.

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

## Running

1. Select a category.
2. Select the brands to run.
3. Enter the requested state, city, and pincode.
4. Click `Run`.
5. Review the table.

Address duplicates are removed automatically before the file is saved. If
shared file storage is configured, the generated Excel file is saved
automatically. When a pincode is entered, matching pincode rows are shown first.

After the table is visible, click `Verify with Google Places` to compare rows
with Google Places. Verified rows are shown first, and unverified rows remain
visible with a Google verification status/reason so they can be reviewed.
Verified rows are sorted by a combined Google rating/review score. Rating and
review count are used only for sorting, not for filtering. If verification is
run, the Google-checked Excel file replaces the previously saved file.

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
- Google verification fields, when Google Places verification is enabled
- Google Directions, when Google Places verification is enabled

`Website` is only for an actual dealer/company website when one is available.
Google Maps, directions, and locator links are kept out of the Website column.

`Google Maps` contains the directions/maps link when the source finds one. If
there is no directions link but latitude and longitude are available, the app
creates a Google Maps link from those coordinates.

## Duplicate Marking

Rows are treated as duplicates when the address matches another row.

Duplicate address rows are removed from newly generated files. In Saved files,
the download checkbox can also remove duplicate address rows from older files.

## Saved Files

If shared file storage is configured for this installation, generated Excel
files are saved automatically. Google-verified files replace the initial saved
file for the same run.

From that tab users can:

- See previously generated files
- Preview an Excel file
- Download a selected file with or without duplicate address rows
- Include or exclude unverified Google rows during download
- Delete a selected file after confirming

Users do not need to enter storage settings in the app.

## Project Files

Important code files and folders:

- `streamlit_app.py` - user interface
- `main.py` - command-line runner
- `catalog.py` - category and brand list
- `brands/` - brand-specific source plugins
- `core/` - shared exporter, schema, registry, and storage code
- `requirements.txt` - Python packages

Generated files are written to `output/`. The folder is kept in the project, but
old exports, screenshots, and error HTML files can be deleted safely.
