"""Deterministic spec extraction from product pages / PDF datasheets. Regex first; ambiguity -> flag."""
import io
import logging
import re

logging.getLogger("pdfminer").setLevel(logging.ERROR)
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import fetch

FREQ = re.compile(r"(\d+(?:\.\d+)?)\s*(?:GHz)?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*GHz", re.I)
FREQ_DC = re.compile(r"\bDC\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*GHz", re.I)
FREQ_MHZ = re.compile(r"(\d+(?:\.\d+)?)\s*(?:MHz)?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*MHz", re.I)
GAIN = re.compile(r"gain[^.\n\r]{0,60}?(\d{1,2}(?:\.\d+)?)\s*dB", re.I)
NOISE_K = re.compile(r"noise\s+temp\w*[^.\n\r]{0,40}?(\d+(?:\.\d+)?)\s*K", re.I)
NOISE_NF = re.compile(r"noise\s+figure[^.\n\r]{0,40}?(\d+(?:\.\d+)?)\s*dB", re.I)
ATTEN = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*dB", re.I)
PRICE = re.compile(r"[$]\s?([\d,]{1,9}(?:\.\d{2})?)")
CRYO = re.compile(r"cryogenic|cryo\b|\b4\s?K\b|milli-?kelvin|\bmK\b|kelvin", re.I)
CONNECTOR = re.compile(r"\b(SMA|2\.92\s?mm|2\.4\s?mm|1\.85\s?mm|K\s?connector|N[- ]type|SMP|GPO|G3PO|SMPM)\b", re.I)
BULKHEAD = re.compile(r"bulkhead|feed-?through|hermetic", re.I)


def page_text(url):
    html = fetch.get(url)
    if not html:
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    pdf = None
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.lower().endswith(".pdf") or "datasheet" in h.lower():
            pdf = urljoin(url, h)
            break
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title + "\n" + soup.get_text(" ", strip=True), pdf


def pdf_text(url, max_pages=4):
    data = fetch.get(url, binary=True)
    if not data or not data[:5].startswith(b"%PDF"):
        return None
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return " ".join((p.extract_text() or "") for p in pdf.pages[:max_pages])
    except Exception:
        return None


def parse_specs(text):
    s = {}
    # earliest match wins: the part's own band (title/top of page) beats related-product noise
    fcands = []
    if m := FREQ.search(text):
        fcands.append((m.start(), [float(m.group(1)), float(m.group(2))]))
    if m := FREQ_DC.search(text):
        fcands.append((m.start(), [0.0, float(m.group(1))]))
    if not fcands and (m := FREQ_MHZ.search(text)):
        fcands.append((m.start(), [float(m.group(1)) / 1000, float(m.group(2)) / 1000]))
    fcands = [(p, f) for p, f in fcands if f[0] < f[1] <= 1000]
    if fcands:
        s["freq_ghz"] = min(fcands)[1]
    if m := GAIN.search(text):
        s["gain_db"] = float(m.group(1))
    if m := NOISE_K.search(text):
        s["noise_k"] = float(m.group(1))
    if m := NOISE_NF.search(text):
        s["noise_figure_db"] = float(m.group(1))
    if m := PRICE.search(text):
        p = float(m.group(1).replace(",", ""))
        if p >= 10:  # ponytail: sub-$10 hits are page noise, not RF part prices
            s["price_usd"] = p
    if m := CONNECTOR.search(text):
        s["connector"] = m.group(1).upper().replace(" ", "")
    s["cryo"] = bool(CRYO.search(text))
    s["bulkhead"] = bool(BULKHEAD.search(text))
    # attenuation: only trust "X dB" values in attenuator context near the word
    att = re.search(r"atten\w*[^.\n\r]{0,30}?(\d{1,2}(?:\.\d+)?)\s*dB", text, re.I)
    if att:
        s["attenuation_db"] = float(att.group(1))
    return s


def excerpt(text, category, n=300):
    """Short window around the most relevant keyword for LLM disambiguation."""
    for kw in ("gain", "noise", "atten", "GHz", category):
        i = text.lower().find(kw.lower())
        if i >= 0:
            return re.sub(r"\s+", " ", text[max(0, i - 60):i + n - 60])
    return re.sub(r"\s+", " ", text[:n])


def enrich(candidate, spec):
    """Fill candidate['specs'] from page (+datasheet PDF if page lacks key specs).
    Sets candidate['flag'] reason when extraction incomplete for ranking."""
    text, pdf = page_text(candidate["url"])
    if not text:
        candidate["error"] = "unreachable"
        return candidate
    specs = parse_specs(text)
    need_more = "freq_ghz" not in specs or (
        spec["category"] in ("lna", "amplifier") and "gain_db" not in specs)
    if need_more and pdf:
        ptext = pdf_text(pdf)
        if ptext:
            specs = {**parse_specs(ptext), **{k: v for k, v in specs.items() if v}}
            candidate["datasheet"] = pdf
            text = ptext + " " + text
    elif pdf:
        candidate["datasheet"] = pdf
    candidate["specs"] = specs
    candidate["kw"] = re.sub(r"\s+", " ", text.lower())[:4000]
    if "freq_ghz" not in specs:
        candidate["flag"] = "no frequency range found"
        candidate["excerpt"] = excerpt(text, spec["category"])
    elif spec.get("gain_db_min") and "gain_db" not in specs:
        candidate["flag"] = "gain not found"
        candidate["excerpt"] = excerpt(text, spec["category"])
    return candidate
