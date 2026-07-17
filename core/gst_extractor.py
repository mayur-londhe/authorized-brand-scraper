"""GST Extractor using Gemini API to extract GSTIN numbers from shop details."""

import os
import re
from typing import Optional
import google.generativeai as genai

# Configure Gemini API
def initialize_gemini():
    """Initialize Gemini API with API key from environment."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    genai.configure(api_key=api_key)


def extract_gstin_from_gemini(shop_name: str, shop_address: str) -> Optional[str]:
    """
    Extract GSTIN number using Gemini API.
    
    Args:
        shop_name: Name of the shop
        shop_address: Address of the shop
    
    Returns:
        GSTIN number if found, None otherwise
    """
    try:
        initialize_gemini()
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""You are a GST (Goods and Services Tax) database expert. 
Extract the GSTIN (Goods and Services Tax Identification Number) for the following shop.

Shop Name: {shop_name}
Shop Address: {shop_address}

Please search for and return ONLY the 15-digit GSTIN number in the format: XX XXXXX XXXX X XXX (with spaces).
If you cannot find the GSTIN, respond with "NOT_FOUND".
Return ONLY the GSTIN number or "NOT_FOUND", nothing else."""

        response = model.generate_content(prompt)
        result = response.text.strip()
        
        # Validate GSTIN format (15 digits with spaces: XX XXXXX XXXX X XXX)
        gstin_pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9]{3}$'
        # Remove spaces for validation
        gstin_clean = result.replace(" ", "")
        
        if gstin_clean == "NOT_FOUND" or not result or result == "NOT_FOUND":
            return None
        
        # Return formatted GSTIN if valid
        if re.match(gstin_pattern, gstin_clean):
            # Format as XX XXXXX XXXX X XXX
            return f"{gstin_clean[:2]} {gstin_clean[2:7]} {gstin_clean[7:11]} {gstin_clean[11]} {gstin_clean[12:15]}"
        
        return None
    except Exception as e:
        print(f"Error extracting GSTIN: {e}")
        return None


def extract_gstins_batch(records: list[dict]) -> list[dict]:
    """
    Extract GSTIN numbers for a batch of shop records.
    
    Args:
        records: List of dicts with 'Shop Name' and 'Shop Address' keys
    
    Returns:
        List of dicts with added 'GSTIN' column
    """
    results = []
    total = len(records)
    
    for index, record in enumerate(records):
        shop_name = str(record.get("Shop Name", "")).strip()
        shop_address = str(record.get("Shop Address", "")).strip()
        
        if not shop_name or not shop_address:
            results.append({**record, "GSTIN": "INVALID_INPUT", "Status": "Missing shop name or address"})
            continue
        
        gstin = extract_gstin_from_gemini(shop_name, shop_address)
        results.append({
            **record,
            "GSTIN": gstin or "NOT_FOUND",
            "Status": "Success" if gstin else "Not Found",
            "Extraction Progress": f"{index + 1}/{total}"
        })
    
    return results
