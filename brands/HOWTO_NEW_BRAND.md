# How to Add a New Brand Plugin

## 1 – Create the file
Copy `crompton.py` → `havells.py` (or whatever the brand is).

## 2 – Set the class attributes
```python
class HavellsHandler(BaseBrandHandler):
    BRAND_NAME = "Havells"                      # must match BRAND_CATEGORY_MAP key in main.py
    SUPPORTED_CATEGORIES = ["fans", "wires"]
```

## 3 – Find the real API endpoint
1. Open the brand's dealer/store-locator page in Chrome
2. Open DevTools → Network tab → filter by **XHR** or **Fetch**
3. Type a state/city and submit the form
4. Look for a request whose response is JSON with dealer data
5. Right-click → Copy as cURL
6. Translate the URL + headers + body into `_api_url()` and `_api_payload()`

## 4 – Map the JSON fields
Inspect the raw JSON response and update `_parse_api_response()`:
```python
name    = item.get("DealerName") or item.get("dealer_name") or item.get("name")
phone   = item.get("ContactNo")  or item.get("phone")
address = item.get("Address1")   + " " + item.get("Address2", "")
```

## 5 – Test it
```bash
python main.py --brand havells --category fans --state Maharashtra
```

## 6 – Assign it to a category
Add "Havells" to the appropriate list in `catalog.py` (`CATEGORY_BRANDS`). Both
the CLI and Streamlit application will pick it up automatically.

## 7 – Open the web application
```bash
streamlit run streamlit_app.py
```

## Tips
- Most store locators use **POST JSON** with `state`/`city` params
- If the site is JS-heavy (React/Vue SPA), intercept XHR rather than scraping HTML
- Pagination: look for `page`, `offset`, or `pageNo` params in the API payload
- Some APIs need a `pincode` instead of city name – use Google Maps Geocoding API to convert
