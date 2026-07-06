"""Cached HTTP fetch. requests first, persistent-playwright fallback for JS/anti-bot.
SQLite cache w/ TTL, per-domain rate limit, one retry on timeout."""
import atexit
import sqlite3
import sys
import threading
import time
from urllib.parse import urlparse

import requests

from .registry import DATA

CACHE_DB = DATA / "cache.db"
TTL = 7 * 86400
FAIL_TTL = 3600
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
VERBOSE = False

_local = threading.local()
_domain_lock = threading.Lock()
_domain_last = {}          # domain -> last request time (politeness: 0.4s/domain)
_pw = {"lock": threading.Lock(), "p": None, "browser": None}


def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr)


def _db():
    if not hasattr(_local, "db"):
        _local.db = sqlite3.connect(CACHE_DB)
        _local.db.execute(
            "CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, ts REAL, ok INT, body BLOB)")
    return _local.db


def _cached(url, ttl):
    row = _db().execute("SELECT ts, ok, body FROM pages WHERE url=?", (url,)).fetchone()
    if row and time.time() - row[0] < (ttl if row[1] else FAIL_TTL):
        return (row[2].decode("utf-8", "replace") if row[1] else None), True
    return None, False


def _store(url, body):
    _db().execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?)",
                  (url, time.time(), 1 if body is not None else 0, (body or "").encode()))
    _db().commit()


def _polite(url):
    domain = urlparse(url).netloc
    with _domain_lock:
        wait = _domain_last.get(domain, 0) + 0.4 - time.time()
        _domain_last[domain] = max(time.time(), _domain_last.get(domain, 0) + 0.4)
    if wait > 0:
        time.sleep(wait)


def _browser():
    if _pw["browser"] is None:
        from playwright.sync_api import sync_playwright
        _pw["p"] = sync_playwright().start()
        _pw["browser"] = _pw["p"].chromium.launch(headless=True)
        atexit.register(_close_browser)
    return _pw["browser"]


def _close_browser():
    try:
        if _pw["browser"]:
            _pw["browser"].close()
        if _pw["p"]:
            _pw["p"].stop()
    except Exception:
        pass


def _render(url):
    with _pw["lock"]:  # ponytail: serialize renders; parallel contexts if ever the bottleneck
        page = _browser().new_page(user_agent=UA)
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            return page.content()
        finally:
            page.close()


def _request(url, render, binary):
    _polite(url)
    if render and not binary:
        return _render(url)
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code in (403, 429, 503) and not binary:
        return _render(url)  # anti-bot -> real browser
    r.raise_for_status()
    return r.content if binary else r.text


def get(url, render=False, ttl=TTL, binary=False):
    """Return page text (bytes if binary), None on failure. Failures cached FAIL_TTL."""
    if not binary:
        body, hit = _cached(url, ttl)
        if hit:
            return body
    body = None
    for attempt in (1, 2):
        try:
            body = _request(url, render, binary)
            break
        except requests.Timeout:
            log(f"timeout ({attempt}) {url}")
        except Exception as e:
            log(f"fetch fail {url}: {type(e).__name__}")
            break
    if binary:
        return body
    _store(url, body)
    return body
