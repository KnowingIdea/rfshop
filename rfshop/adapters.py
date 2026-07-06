"""Candidate product-page discovery per vendor (sitemap crawl or site search)."""
import re
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from . import fetch
from .registry import synonyms

MAX_SITEMAPS = 8
MAX_CANDIDATES = 25

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
                 "/product-sitemap.xml", "/sitemap/sitemap.xml"]


# pages that are never products
JUNK_WORDS = {"about", "contact", "team", "privacy", "careers", "career", "news", "blog",
              "policy", "distributors", "query", "quote", "quotes", "login", "cart",
              "account", "terms", "warranty", "faq", "events", "press", "openpositions",
              "sitemap", "home", "search", "history", "library", "publications",
              "paper", "papers", "article", "articles", "industries", "industry",
              "markets", "market", "applications", "capabilities", "exhibit",
              "conference", "launch", "launches", "partnership", "signs", "study"}
# a slug naming a *different* part type disqualifies (isolators in an LNA search)
PART_WORDS = {"isolator", "circulator", "attenuator", "mixer", "coupler", "switch",
              "termination", "filter", "amplifier", "lna", "cable", "connector",
              "adapter", "feedthrough", "detector"}
# slugs made only of these words are category/listing pages, not parts
GENERIC_WORDS = {"product", "products", "shop", "catalog", "components", "component",
                 "general", "purpose", "rf", "microwave", "cryogenic", "cryo", "power",
                 "in", "stock", "ready", "to", "ship", "outlines", "options", "custom",
                 "configurations", "subsystems", "family", "information", "amplifier",
                 "amplifiers", "attenuator", "attenuators", "low", "noise", "lna",
                 "circulator", "circulators", "isolator", "isolators", "filter", "filters",
                 "mixer", "mixers", "coupler", "couplers", "switch", "switches", "cable",
                 "cables", "connector", "connectors", "adapter", "adapters", "termination",
                 "terminations", "feedthrough", "feedthroughs"}


def _slug_tokens(spec):
    """Keywords to match in URL paths/link text: category synonyms + band + 'cryo' if cold."""
    toks = [s.replace(" ", "-") for s in synonyms(spec["category"])]
    toks += [s.replace("-", "") for s in toks]
    if (spec.get("temp_k") or 300) <= 77:
        toks += ["cryo", "cryogenic"]
    if spec.get("freq_ghz"):
        lo, hi = spec["freq_ghz"]
        toks += [f"{lo:g}-{hi:g}", f"{lo:g}_{hi:g}", f"{lo:g}to{hi:g}"]
    return list(dict.fromkeys(t.lower() for t in toks))


def _matches(text, toks):
    t = text.lower().replace("_", "-")
    return any(k in t or k in t.replace("-", "") for k in toks)


def _url_ok(url, toks, category=None):
    """Match against path only (not domain); drop junk, listing slugs, other part types."""
    path = urlparse(url).path.lower()
    if not _matches(path, toks):
        return False
    slug = re.sub(r"\.(html?|php|aspx?)$", "", path.rstrip("/").rsplit("/", 1)[-1])
    path_words = [w.rstrip("s") for w in re.split(r"[-_./]+", path) if w]
    if any(w in JUNK_WORDS or w + "s" in JUNK_WORDS for w in path_words):
        return False
    words = [w.rstrip("s") for w in re.split(r"[-_.]+", slug) if w]
    if words and all(w in GENERIC_WORDS or w + "s" in GENERIC_WORDS for w in words):
        return False  # listing page like /power-amplifiers/
    if category:
        own = {w for s in synonyms(category) for w in re.split(r"[\s-]+", s)}
        own |= {w.rstrip("s") for w in own}
        if any(w in PART_WORDS and w not in own for w in words):
            return False
    return True


def _robots_sitemaps(base):
    txt = fetch.get(base.rstrip("/") + "/robots.txt")
    if not txt or "<html" in txt[:200].lower():
        return []
    return re.findall(r"(?im)^sitemap:\s*(\S+)", txt)


def _sitemap_urls(base):
    """All URLs from vendor sitemap(s): robots.txt-declared first, then common paths."""
    candidates = _robots_sitemaps(base) + [base.rstrip("/") + p for p in SITEMAP_PATHS]
    for sm in candidates:
        xml = fetch.get(sm)
        if not xml or "<" not in xml:
            continue
        urls = _expand_sitemap(xml, depth=2)
        if urls:
            return urls
    return []


def _expand_sitemap(xml, depth):
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)
    if "<sitemapindex" not in xml or depth == 0:
        return locs
    urls = []
    # prefer product/page child sitemaps
    children = sorted(locs, key=lambda u: 0 if re.search(r"product|page", u) else 1)
    for child in children[:MAX_SITEMAPS]:
        cxml = fetch.get(child)
        if cxml:
            urls += _expand_sitemap(cxml, depth - 1)
    return urls


def _from_sitemap(vendor, spec):
    toks = _slug_tokens(spec)
    urls = _sitemap_urls(vendor["url"])
    hits = [u for u in urls if _url_ok(u, toks, spec["category"])]
    # product-detail pages first; short slugs (single parts) before long system bundles
    hits.sort(key=lambda u: (0 if re.search(r"/(product|shop|part)", u) else 1, len(u)))
    return [{"url": u, "title": u.rstrip("/").rsplit("/", 1)[-1]} for u in hits[:MAX_CANDIDATES]]


def _from_search(vendor, spec):
    q = " ".join(w for w in ["cryogenic" if (spec.get("temp_k") or 300) <= 77 else "",
                             spec["category"].replace("_", " ")] if w)
    if spec.get("freq_ghz"):
        q += f" {spec['freq_ghz'][0]}-{spec['freq_ghz'][1]} GHz"
    html = fetch.get(vendor["search_url"].format(q=quote_plus(q)),
                     render=vendor.get("render", False))
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    toks = _slug_tokens(spec)
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(vendor["url"], a["href"])
        text = a.get_text(" ", strip=True)
        if href in seen or not href.startswith("http"):
            continue
        if _url_ok(href, toks, spec["category"]) or (
                text and _matches(text, toks) and _url_ok(href, [""], spec["category"])):
            seen.add(href)
            out.append({"url": href, "title": text or href.rsplit("/", 1)[-1]})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def candidates(vendor, spec):
    """Return [{url, title}] candidate product pages for this vendor. Never raises."""
    try:
        if vendor.get("strategy") == "search" and vendor.get("search_url"):
            return _from_search(vendor, spec)
        return _from_sitemap(vendor, spec)
    except Exception:
        return []
