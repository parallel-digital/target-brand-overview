import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
from urllib.parse import urljoin, urlencode, urlparse, parse_qs
from io import BytesIO

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_URL = "https://www.target.com/s"
BASE_URL = "https://www.target.com"


def build_search_url(query, page=1):
    # Target uses simple query params: searchTerm and page
    params = {"searchTerm": query, "page": page}
    return f"{SEARCH_URL}?{urlencode(params)}"


def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.text


def find_product_links_from_search(soup):
    links = set()
    # Try several heuristics because Target markup changes
    # 1) anchors that contain '/p/' in href AND have data-test or aria-label
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/p/" in href:
            # canonicalize
            full = urljoin(BASE_URL, href.split('?')[0])
            links.add(full)
    # fallback: look for <link rel="canonical"> within product cards
    return list(links)


def extract_product_data(product_url, page_text=None):
    # Fetch page if text not provided
    try:
        if page_text is None:
            soup, page_text = get_soup(product_url)
        else:
            soup = BeautifulSoup(page_text, "html.parser")
    except Exception as e:
        return {"url": product_url, "error": str(e)}

    data = {"url": product_url}

    # Title
    title_tag = soup.find("meta", property="og:title")
    if title_tag and title_tag.get("content"):
        data["title"] = title_tag["content"].strip()
    else:
        h1 = soup.find(["h1", "h2"], attrs={"data-test": "product-title"})
        data["title"] = h1.get_text(strip=True) if h1 else None

    # Price - try meta, then JSON-LD, then visible price selectors
    price = None
    price_meta = soup.find("meta", attrs={"property": "product:price:amount"})
    if price_meta and price_meta.get("content"):
        price = price_meta["content"].strip()
    # JSON-LD
    if not price:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                txt = script.string
                if not txt:
                    continue
                if 'price' in txt:
                    m = re.search(r'"price"\s*:\s*"?([0-9]+\.?[0-9]*)"?', txt)
                    if m:
                        price = m.group(1)
                        break
            except Exception:
                continue
    # visible price
    if not price:
        price_span = soup.find("span", attrs={"data-test": "product-price"})
        if price_span:
            price = price_span.get_text(strip=True)
    data["price"] = price

    # Ratings & review count - try JSON-LD first
    rating = None
    reviews = None
    for script in soup.find_all("script", type="application/ld+json"):
        txt = script.string
        if not txt:
            continue
        # ratingValue
        m = re.search(r'"ratingValue"\s*:\s*"?([0-9]+\.?[0-9]*)"?', txt)
        if m:
            rating = m.group(1)
        m2 = re.search(r'"reviewCount"\s*:\s*"?(\d+)"?', txt)
        if m2:
            reviews = m2.group(1)
        if rating or reviews:
            break

    # fallback: look for aria-labels like '4.5 out of 5 stars'
    if not rating:
        star = soup.find(lambda tag: tag.name in ["span", "div"] and tag.get("aria-label") and "out of 5" in tag.get("aria-label"))
        if star:
            m = re.search(r'([0-9]+\.?[0-9]*)\s*out of 5', star["aria-label"])
            if m:
                rating = m.group(1)

    if not reviews:
        rc = soup.find(lambda tag: tag.name in ["span", "div"] and tag.get("aria-label") and "reviews" in tag.get("aria-label"))
        if rc:
            m = re.search(r'([0-9,]+)\s*reviews', rc["aria-label"].replace('\xa0', ' '))
            if m:
                reviews = m.group(1).replace(',', '')

    data["rating"] = rating
    data["reviews"] = reviews

    # TCIN - search page text
    tcin = None
    # common patterns
    m = re.search(r'"tcin"\s*:\s*"?(\d{6,12})"?', page_text)
    if m:
        tcin = m.group(1)
    else:
        m2 = re.search(r'"tcin"\s*:\s*(\d{6,12})', page_text)
        if m2:
            tcin = m2.group(1)
    data["tcin"] = tcin

    # Brand - try meta
    brand = None
    brand_meta = soup.find("meta", attrs={"property": "og:brand"})
    if brand_meta and brand_meta.get("content"):
        brand = brand_meta["content"].strip()
    else:
        brand_tag = soup.find(lambda tag: tag.name in ["a", "span"] and tag.get_text() and "Brand" in tag.get_text())
        brand = None if not brand_tag else brand_tag.get_text(strip=True)
    data["brand"] = brand

    return data


def scrape_brand(brand_or_url, max_pages=20, delay=1.0):
    # if input is a full brand listing URL, try to extract searchTerm or use it directly
    parsed = urlparse(brand_or_url)
    urls = []
    if parsed.scheme and parsed.netloc and "target.com" in parsed.netloc:
        # it's a URL. We'll fetch and attempt to find product links
        try:
            soup, text = get_soup(brand_or_url)
            urls.extend(find_product_links_from_search(soup))
        except Exception as e:
            st.error(f"Failed to load provided URL: {e}")
            return []
    else:
        # treat as brand name
        page = 1
        while page <= max_pages:
            search_url = build_search_url(brand_or_url, page=page)
            try:
                soup, text = get_soup(search_url)
            except Exception as e:
                st.warning(f"Stopping at page {page} due to error: {e}")
                break
            links = find_product_links_from_search(soup)
            if not links:
                # no more results
                break
            # only add new links
            new = [l for l in links if l not in urls]
            urls.extend(new)
            st.info(f"Found {len(new)} new products on page {page} (total {len(urls)})")
            page += 1
            time.sleep(delay)

    # now visit each product page and extract data
    results = []
    for i, url in enumerate(urls, 1):
        st.info(f"Scraping {i}/{len(urls)}")
        try:
            data = extract_product_data(url)
        except Exception as e:
            data = {"url": url, "error": str(e)}
        results.append(data)
        time.sleep(delay)

    return results


# --- Streamlit UI ---
st.set_page_config(page_title="Target Brand Scraper (Tool 1)", layout="wide")
st.title("Target Brand / Listing Scraper — Tool 1")
st.markdown(
    "Enter a brand name (e.g. **Yoobi**) or paste a Target brand/listing URL. The app will collect product URLs from the listing or search results and then visit each product page to extract title, price, rating, reviews, TCIN, and brand." 
)

col1, col2 = st.columns([3,1])
with col1:
    brand_input = st.text_input("Brand name or Target URL", value="Yoobi")
with col2:
    max_pages = st.number_input("Max search pages to crawl", min_value=1, max_value=50, value=5)
    delay = st.number_input("Delay between requests (seconds)", min_value=0.1, max_value=10.0, value=0.8, step=0.1)

if st.button("Start Scrape"):
    if not brand_input:
        st.error("Enter a brand name or URL first.")
    else:
        with st.spinner("Scraping — this may take a while depending on pages and products..."):
            results = scrape_brand(brand_input, max_pages=max_pages, delay=delay)
        if not results:
            st.warning("No products found or scraping failed.")
        else:
            df = pd.DataFrame(results)
            # normalize columns
            cols = ["title", "price", "rating", "reviews", "tcin", "brand", "url", "error"]
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            df = df[cols]

            st.success(f"Scrape complete — {len(df)} items")
            st.dataframe(df)

            # CSV download
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(label="Download CSV", data=csv, file_name="target_brand_products.csv", mime="text/csv")

            # Excel download
            try:
                towrite = BytesIO()
                with pd.ExcelWriter(towrite, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="products")
                towrite.seek(0)
                st.download_button(label="Download XLSX", data=towrite, file_name="target_brand_products.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception:
                pass

st.markdown("---")
st.markdown(
    "**Notes & next steps:**\n\n- This script uses static HTTP requests + HTML parsing (requests + BeautifulSoup). Target's site is dynamic and may change; if results are empty, consider using Playwright/Selenium to render JS.\n- TCIN is extracted heuristically from embedded JSON within product pages; if you need more reliable results we can switch to calling Target's internal endpoints or running a headless browser.\n- I recommend running this behind a corporate IP or a permitted infrastructure and keeping request rates low to avoid blocking.\n"
)

st.markdown("**Dependencies:** `streamlit`, `requests`, `beautifulsoup4`, `pandas`, `openpyxl`\n\nRun: `pip install streamlit requests beautifulsoup4 pandas openpyxl`\nThen: `streamlit run streamlit_target_brand_scraper.py`")
