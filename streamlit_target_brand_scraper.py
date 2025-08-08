# save as streamlit_target_brand_scraper_fixed.py
import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import json
import time
from urllib.parse import urljoin, urlparse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.target.com/",
}
PAGE_SIZE = 24

st.set_page_config(page_title="Target Product Scraper (robust)", layout="wide")
st.title("Target Brand / Listing Scraper — robust (HTML + optional Redsky API)")
st.write("Paste a Target brand/category URL (e.g. https://www.target.com/b/yoobi/-/N-551o8). The app will try the fast Redsky API first (optional) and fall back to HTML pagination (?Nao=) if needed.")

def extract_category_id(url: str):
    m = re.search(r"/N-([A-Za-z0-9]+)", url)
    return m.group(1) if m else None

def get_soup(url, retries=2, timeout=15):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser"), resp.text
        except Exception as e:
            if attempt + 1 == retries:
                raise
            time.sleep(0.5)

def try_redsky_api(category_id, max_pages=20):
    keys = [
        "ff457966e64d5e877fdbad070f276d18ecec4a01",
        "ff457b18e5e8f697f4a0e3f1f4f9c3c7",
    ]
    base = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
    for key in keys:
        rows = []
        for page in range(max_pages):
            params = {
                "key": key,
                "channel": "WEB",
                "count": str(PAGE_SIZE),
                "offset": str(page * PAGE_SIZE),
                "page": f"/b/yoobi/-/N-{category_id}",
                "platform": "desktop",
                "pricing_store_id": "3991",
                "store_ids": "3991",
                "useragent": HEADERS["User-Agent"],
            }
            try:
                r = requests.get(base, params=params, headers=HEADERS, timeout=15)
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("data", {}).get("search", {}).get("products", [])
                if not items:
                    break
                for p in items:
                    try:
                        tcin = p.get("tcin") or p.get("item", {}).get("tcin")
                        title = p.get("item", {}).get("product_description", {}).get("title")
                        price = p.get("price", {}).get("current_retail") if p.get("price") else None
                        rating = p.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("average")
                        reviews = p.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("count")
                        buy_url = p.get("item", {}).get("enrichment", {}).get("buy_url") or (f"https://www.target.com/p/-/A-{tcin}" if tcin else None)
                        rows.append({"title": title, "price": price, "rating": rating, "reviews": reviews, "tcin": tcin, "url": buy_url})
                    except Exception:
                        continue
            except Exception:
                break
        if rows:
            return pd.DataFrame(rows)
    return pd.DataFrame([])

def parse_listing_pages(base_url, max_pages=50, delay=0.6):
    found = []
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    offset = 0
    page_count = 0
    while page_count < max_pages:
        page_url = f"{base}?Nao={offset}" if offset else base
        try:
            soup, txt = get_soup(page_url)
        except Exception as e:
            st.warning(f"Failed to fetch listing page {page_url}: {e}")
            break
        anchors = soup.find_all('a', href=True)
        page_links = set()
        for a in anchors:
            href = a['href']
            if '/p/' in href and ('/A-' in href or re.search(r'/-?/A-\\d+', href)):
                full = urljoin(f"https://{parsed.netloc}", href.split('?')[0])
                page_links.add(full)
        if not page_links:
            for a in anchors:
                href = a['href']
                if '/p/' in href:
                    full = urljoin(f"https://{parsed.netloc}", href.split('?')[0])
                    page_links.add(full)
        new_links = [l for l in page_links if l not in found]
        if not new_links:
            break
        found.extend(sorted(new_links))
        st.info(f"Page {page_count+1}: found {len(new_links)} products (total {len(found)})")
        offset += PAGE_SIZE
        page_count += 1
        time.sleep(delay)
    return found

def extract_product_details(product_url, retries=2):
    try:
        soup, txt = get_soup(product_url, retries=retries)
    except Exception as e:
        return {"url": product_url, "error": str(e)}
    out = {"url": product_url}
    m = re.search(r'/A-(\\d+)', product_url)
    if m:
        out['tcin'] = m.group(1)
    else:
        mm = re.search(r'"tcin"\\s*:\\s*"?(\d{6,12})"?', txt)
        out['tcin'] = mm.group(1) if mm else None
    og = soup.find('meta', property='og:title')
    if og and og.get('content'):
        out['title'] = og['content'].strip()
    else:
        h1 = soup.find(['h1', 'h2'])
        out['title'] = h1.get_text(strip=True) if h1 else None
    try:
        scripts = soup.find_all('script', type='application/ld+json')
        for s in scripts:
            if not s.string:
                continue
            try:
                payload = json.loads(s.string)
            except Exception:
                continue
            if isinstance(payload, dict):
                offers = payload.get('offers')
                if offers and isinstance(offers, dict):
                    out['price'] = offers.get('price') or offers.get('priceSpecification', {}).get('price')
                agg = payload.get('aggregateRating')
                if agg:
                    out['rating'] = agg.get('ratingValue')
                    out['reviews'] = agg.get('reviewCount')
    except Exception:
        pass
    if 'price' not in out or not out.get('price'):
        m = re.search(r'"current_retail"\\s*:\\s*([0-9]+\\.?[0-9]*)', txt)
        if m:
            out['price'] = m.group(1)
        else:
            price_tag = soup.find(attrs={"data-test": "product-price"})
            out['price'] = price_tag.get_text(strip=True) if price_tag else None
    if 'rating' not in out or not out.get('rating'):
        m = re.search(r'"ratingValue"\\s*:\\s*"?([0-9]+\\.?[0-9]*)"?', txt)
        if m:
            out['rating'] = m.group(1)
    if 'reviews' not in out or not out.get('reviews'):
        m = re.search(r'"reviewCount"\\s*:\\s*"?(\d+)"?', txt)
        if m:
            out['reviews'] = m.group(1)
    return out

col1, col2 = st.columns([3,1])
with col1:
    url_input = st.text_input("Target brand/category URL (or paste product URL)", value="https://www.target.com/b/yoobi/-/N-551o8")
with col2:
    max_pages = st.number_input("Max listing pages to crawl", min_value=1, max_value=100, value=8)
    delay = st.number_input("Delay between page requests (s)", min_value=0.1, max_value=5.0, value=0.6, step=0.1)
    use_redsky = st.checkbox("Try Redsky API first (faster)", value=True)

if st.button("Start Scrape"):
    if not url_input:
        st.error("Enter a Target URL first")
    else:
        with st.spinner("Running scrape — this may take a few minutes for many SKUs..."):
            category_id = extract_category_id(url_input)
            df = pd.DataFrame()
            if use_redsky and category_id:
                try:
                    df = try_redsky_api(category_id, max_pages=max_pages)
                    if not df.empty:
                        st.success(f"Redsky API returned {len(df)} items — using that result")
                except Exception as e:
                    st.warning(f"Redsky attempt failed: {e}")
            if df.empty:
                try:
                    product_urls = parse_listing_pages(url_input, max_pages=max_pages, delay=delay)
                except Exception as e:
                    st.error(f"Failed to parse listing pages: {e}")
                    product_urls = []
                if not product_urls:
                    st.error("No products found via HTML listing. Check the URL or try toggling the Redsky option.")
                else:
                    rows = []
                    for i, purl in enumerate(product_urls, 1):
                        st.info(f"Scraping product {i}/{len(product_urls)}")
                        try:
                            info = extract_product_details(purl)
                        except Exception as e:
                            info = {"url": purl, "error": str(e)}
                        rows.append(info)
                        time.sleep(0.3)
                    df = pd.DataFrame(rows)
            if not df.empty:
                for c in ["title", "price", "rating", "reviews", "tcin", "url", "error"]:
                    if c not in df.columns:
                        df[c] = None
                df = df[[c for c in ["title", "price", "rating", "reviews", "tcin", "url", "error"] if c in df.columns]]
                st.success(f"Completed — {len(df)} items")
                st.dataframe(df)
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV", csv, "target_brand_products.csv", "text/csv")
                try:
                    from io import BytesIO
                    towrite = BytesIO()
                    with pd.ExcelWriter(towrite, engine="openpyxl") as writer:
                        df.to_excel(writer, index=False, sheet_name="products")
                    towrite.seek(0)
                    st.download_button("Download XLSX", towrite, "target_brand_products.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception:
                    pass
            else:
                st.warning("No items to show after scraping.")
