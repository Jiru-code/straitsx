"""Discovery: scans a product page and extracts name + price.

Looks for common structured-data patterns (Open Graph / product meta tags)
first, then falls back to naive heuristics on page text. This is a
best-effort demo scraper, not a general-purpose one -- for a real hackathon
build you'd likely swap this for a proper product-search/browsing tool.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup


@dataclass
class ProductListing:
    title: str
    price_sgd: float
    url: str


def _load_html(url: str) -> str:
    if url.startswith("file://") or Path(url).exists():
        path = url.replace("file://", "")
        return Path(path).read_text()
    resp = httpx.get(url, timeout=10.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def discover_product(url: str) -> ProductListing:
    html = _load_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find(id="product-title") or soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Unknown item"

    price_meta = soup.find("meta", attrs={"name": "product:price:amount"})
    if price_meta and price_meta.get("content"):
        price = float(price_meta["content"])
    else:
        price_tag = soup.find(id="product-price")
        text = price_tag.get_text(strip=True) if price_tag else soup.get_text()
        match = re.search(r"(?:SGD|S\$)\s*([\d,]+\.\d{2})", text)
        if not match:
            raise ValueError(f"Could not find an SGD price on page: {url}")
        price = float(match.group(1).replace(",", ""))

    return ProductListing(title=title, price_sgd=price, url=url)
