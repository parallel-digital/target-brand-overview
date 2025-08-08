import streamlit as st
import requests
import pandas as pd
import re

st.set_page_config(page_title="Target Product Scraper", layout="wide")
st.title("🛒 Target Product Scraper (API-based)")

def extract_category_id(url):
    """
    Extracts the category ID from a Target URL.
    Example: https://www.target.com/b/yoobi/-/N-551o8  -> 551o8
    """
    match = re.search(r'/N-([a-z0-9]+)', url)
    return match.group(1) if match else None

def fetch_target_products(category_id, max_pages=50):
    """
    Fetch products from Target API for the given category_id.
    Loops through paginated results until no more products are found.
    """
    all_products = []
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"

    params = {
        "key": "ff457b18e5e8f697f4a0e3f1f4f9c3c7",  # Public key used in browser requests
        "channel": "WEB",
        "count": 24,
        "offset": 0,
        "page": "/b/yoobi/-/N-" + category_id,
        "platform": "desktop",
        "pricing_store_id": "3991",
        "scheduled_delivery_store_id": "3991",
        "store_ids": "3991",
        "useragent": "Mozilla/5.0",
        "visitor_id": "016F5F6FA59C0201A4D39E8E8B123ABC",
        "zip": "90210"
    }

    for page in range(max_pages):
        params["offset"] = page * params["count"]
        resp = requests.get(base_url, params=params)

        if resp.status_code != 200:
            break

        data = resp.json()
        items = data.get("data", {}).get("search", {}).get("products", [])
        if not items:
            break

        for product in items:
            try:
                all_products.append({
                    "Title": product.get("item", {}).get("product_description", {}).get("title", ""),
                    "Price": product.get("price", {}).get("current_retail", None),
                    "Star Rating": product.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("average", None),
                    "# Reviews": product.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("count", None),
                    "TCIN": product.get("tcin", ""),
                    "URL": f"https://www.target.com/p/-/A-{product.get('tcin', '')}"
                })
            except Exception:
                pass

    return pd.DataFrame(all_products)

# Input
url_input = st.text_input("Enter Target category or brand URL", "")

if url_input:
    category_id = extract_category_id(url_input)
    if category_id:
        st.write(f"**Extracted Category ID:** {category_id}")
        with st.spinner("Fetching product data..."):
            df = fetch_target_products(category_id)
        if not df.empty:
            st.success(f"Found {len(df)} products.")
            st.dataframe(df)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV", csv, "target_products.csv", "text/csv")
        else:
            st.error("No products found. The category may be empty or URL is incorrect.")
    else:
        st.error("Could not extract category ID from URL. Please check the format.")
