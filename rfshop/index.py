"""Deep per-vendor product index. Full-catalog crawl once, cached 7 days in index.db.
Query = text match over title/url/page keywords — immune to slug-naming misses."""
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import extract, fetch
from .adapters import _sitemap_urls, _url_ok
from .registry import DATA, synonyms

INDEX_DB = DATA / "index.db"
MAX_AGE = 7 * 86400
MAX_INDEX_PAGES = 1000    # ponytail: bigger catalogs fall back to slug filtering (QMC=931 fits)
KW_CHARS = 4000

_local = threading.local()


def _db():
    if not hasattr(_local, "db"):
        _local.db = sqlite3.connect(INDEX_DB)
        _local.db.execute("""CREATE TABLE IF NOT EXISTS products
            (url TEXT PRIMARY KEY, vendor TEXT, title TEXT, specs TEXT, kw TEXT, ts REAL)""")
        _local.db.execute("CREATE TABLE IF NOT EXISTS crawls (vendor TEXT PRIMARY KEY, ts REAL)")
    return _local.db


def _index_page(vendor_name, url):
    text, pdf = extract.page_text(url)
    if not text:
        return None
    title = text.split("\n", 1)[0][:120].strip() or url.rstrip("/").rsplit("/", 1)[-1]
    specs = extract.parse_specs(text)
    if pdf:
        specs["_datasheet"] = pdf
    kw = re.sub(r"\s+", " ", text.lower())[:KW_CHARS]
    return (url, vendor_name, title, json.dumps(specs), kw, time.time())


def build(vendor, force=False):
    """Crawl vendor's full product catalog into the index. Returns #pages indexed."""
    db = _db()
    row = db.execute("SELECT ts FROM crawls WHERE vendor=?", (vendor["name"],)).fetchone()
    if row and time.time() - row[0] < MAX_AGE and not force:
        return -1  # fresh
    urls = [u for u in _sitemap_urls(vendor["url"]) if _url_ok(u, [""])]
    if len(urls) > MAX_INDEX_PAGES:
        return -2  # catalog too big to deep-index; caller falls back to slug filtering
    fetch.log(f"indexing {vendor['name']}: {len(urls)} pages")
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(lambda u: _index_page(vendor["name"], u), urls):
            if r:
                rows.append(r)
    db.execute("DELETE FROM products WHERE vendor=?", (vendor["name"],))
    db.executemany("INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?)", rows)
    db.execute("INSERT OR REPLACE INTO crawls VALUES (?,?)", (vendor["name"], time.time()))
    db.commit()
    return len(rows)


def query(vendor, spec):
    """Candidates from a vendor's index: category named in title/url, mentioned repeatedly
    in page text, or band token in url/title."""
    syns = [s.lower() for s in synonyms(spec["category"])]
    band = []
    if spec.get("freq_ghz"):
        lo, hi = spec["freq_ghz"]
        band = [f"{lo:g}-{hi:g}", f"{lo:g}_{hi:g}", f"{lo:g} to {hi:g}", f"{lo:g} - {hi:g}"]
    out = []
    for url, title, specs, kw in _db().execute(
            "SELECT url, title, specs, kw FROM products WHERE vendor=?", (vendor["name"],)):
        hay = (title + " " + url).lower().replace("_", "-")
        hit = any(s in hay or s.replace(" ", "-") in hay or s.replace(" ", "") in hay
                  for s in syns)
        if not hit and kw:
            hit = sum(kw.count(s) for s in syns) >= 2
        if not hit and band:
            hit = any(b in hay for b in band)
        if not hit:
            continue
        if not _url_ok(url, [""], spec["category"]):
            continue  # junk page or names a different part type
        c = {"url": url, "title": title, "vendor": vendor["name"],
             "specs": json.loads(specs), "kw": kw}
        if "_datasheet" in c["specs"]:
            c["datasheet"] = c["specs"].pop("_datasheet")
        out.append(c)
    return out


def status():
    """[(vendor, pages, age_days)] for indexed vendors."""
    return [(v, n, round((time.time() - ts) / 86400, 1)) for v, n, ts in _db().execute(
        "SELECT c.vendor, COUNT(p.url), c.ts FROM crawls c LEFT JOIN products p "
        "ON p.vendor=c.vendor GROUP BY c.vendor").fetchall()]
