"""CLI: search / rerank / inspect / index / doctor / contact / web."""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import adapters, extract, fetch, index, rank
from .registry import DATA, load_vendors, vendors_for

SEARCH_CAP = 25      # per search-strategy vendor (site-search result pages enriched)
REFINE_TOP = 40      # top results eligible for datasheet-PDF refinement
FLAG_TOP = 30


def _indexed_candidates(vendor, spec):
    if index.build(vendor) == -2:  # catalog too big: slug-filter sitemap instead
        return _searched_candidates(vendor, spec)
    cands = index.query(vendor, spec)
    for c in cands:
        c["vendor_lead"] = vendor.get("lead_weeks")
    return cands


def _searched_candidates(vendor, spec):
    cands = adapters.candidates(vendor, spec)[:SEARCH_CAP]
    for c in cands:
        c["vendor"] = vendor["name"]
        c["vendor_lead"] = vendor.get("lead_weeks")
        extract.enrich(c, spec)
    return cands


def _refine(results, spec):
    """Datasheet-PDF pass for top results missing a decisive spec."""
    key_missing = lambda s: ("freq_ghz" not in s
                             or (spec.get("gain_db_min") and "gain_db" not in s)
                             or (spec.get("attenuation_db") is not None
                                 and "attenuation_db" not in s))
    for c in results[:REFINE_TOP]:
        s = c.get("specs", {})
        if not key_missing(s):
            continue
        pdf = c.get("datasheet")
        if not pdf:
            _, pdf = extract.page_text(c["url"])
        if not pdf:
            continue
        ptext = extract.pdf_text(pdf)
        if ptext:
            c["datasheet"] = pdf
            for k, v in extract.parse_specs(ptext).items():
                s.setdefault(k, v)
            c["specs"] = s


def _write_outputs(results, spec, errors):
    flags = []
    for c in results[:FLAG_TOP]:
        s = c.get("specs", {})
        if "freq_ghz" not in s or (c["unknown"] and c["tier"] >= 1):
            flags.append({"url": c["url"], "vendor": c["vendor"], "title": c["title"],
                          "unverified": c["unknown"], "excerpt": (c.get("kw") or "")[:300]})
    (DATA / "results.json").write_text(json.dumps(
        {"spec": spec, "results": [{k: v for k, v in c.items() if k != "kw"}
                                   for c in results]}, indent=1))
    (DATA / "flags.json").write_text(json.dumps(flags, indent=1))
    md = rank.markdown(results, spec, errors)
    (DATA / "results.md").write_text(md)
    print(md)
    print(f"[{len(results)} options | results: {DATA/'results.md'} | "
          f"{len(flags)} unverified -> {DATA/'flags.json'}]")


def search(spec_path):
    spec = json.loads(open(spec_path).read())
    vendors = vendors_for(spec["category"])
    if not vendors:
        sys.exit(f"no vendors for category {spec['category']!r}")
    backstop = [v["name"] for v in vendors if v.get("strategy") == "backstop"]
    vendors = [v for v in vendors if v.get("strategy") != "backstop"]
    indexed = [v for v in vendors if v.get("strategy") != "search"]
    searched = [v for v in vendors if v.get("strategy") == "search"]
    all_cands, errors = [], set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_indexed_candidates, v, spec): v for v in indexed}
        futs |= {ex.submit(_searched_candidates, v, spec): v for v in searched}
        for f, v in futs.items():
            try:
                cands = f.result()
            except Exception as e:
                fetch.log(f"{v['name']}: {type(e).__name__} {e}")
                cands = []
            if not cands:
                errors.add(v["name"])
            all_cands += cands
    seen = set()
    all_cands = [c for c in all_cands if not (c["url"] in seen or seen.add(c["url"]))]
    results = rank.rank(all_cands, spec)
    _refine(results, spec)
    results = rank.rank(results, spec)
    _write_outputs(results, spec, errors)
    if backstop:
        print("Backstop vendors (not scrapeable — cover via WebSearch + `rfshop inspect`): "
              + ", ".join(backstop))


def rerank(resolutions_path):
    data = json.loads((DATA / "results.json").read_text())
    fixes = {r["url"]: r["specs"] for r in json.loads(open(resolutions_path).read())}
    for c in data["results"]:
        if c["url"] in fixes:
            c.setdefault("specs", {}).update(fixes[c["url"]])
    results = rank.rank(data["results"], data["spec"])
    _write_outputs(results, data["spec"], set())


def inspect(urls):
    """Enrich ad-hoc URLs (e.g. from WebSearch) and merge into current results."""
    data = json.loads((DATA / "results.json").read_text())
    spec = data["spec"]
    domains = {urlparse(v["url"]).netloc.removeprefix("www."): v["name"]
               for v in load_vendors()}
    for url in urls:
        vendor = domains.get(urlparse(url).netloc.removeprefix("www."), urlparse(url).netloc)
        c = {"url": url, "title": url.rstrip("/").rsplit("/", 1)[-1], "vendor": vendor}
        extract.enrich(c, spec)
        if c.get("kw"):
            c["title"] = c["kw"][:80]
        data["results"].append(c)
    results = rank.rank(data["results"], spec)
    _write_outputs(results, spec, set())


def rebuild_index(vendor_name):
    for v in load_vendors():
        if not vendor_name or vendor_name.lower() in v["name"].lower():
            if v.get("strategy") != "search":
                n = index.build(v, force=True)
                print(f"{v['name']}: {n} pages")


def doctor():
    """Probe every vendor; one status line each."""
    def probe(v):
        try:
            if v.get("strategy") == "backstop":
                return v["name"], "backstop (skill WebSearch)"
            if v.get("strategy") == "search":
                spec = {"category": v["categories"][0], "temp_k": 300}
                n = len(adapters.candidates(v, spec))
                return v["name"], f"search: {n} hits" + ("  ⚠ BROKEN" if n == 0 else "")
            n = len(adapters._sitemap_urls(v["url"]))
            return v["name"], f"sitemap: {n} urls" + ("  ⚠ BROKEN" if n == 0 else "")
        except Exception as e:
            return v["name"], f"⚠ ERROR {type(e).__name__}"
    with ThreadPoolExecutor(max_workers=8) as ex:
        for name, status in ex.map(probe, load_vendors()):
            print(f"{name:28} {status}")


def setup():
    """One-shot post-install: browser + registry health."""
    import subprocess
    print("installing chromium for playwright (skips if present)...")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    print("\nvendor registry health:")
    doctor()
    print(f"\nready. data dir: {DATA}\n  rfshop search spec.json | rfshop web")


def contact(vendor_name):
    for v in load_vendors():
        if vendor_name.lower() in v["name"].lower():
            print(f"{v['name']}: {v.get('rfq_email') or v['url'] + ' (web form)'}")
            return
    print("vendor not found")


def main():
    p = argparse.ArgumentParser(prog="rfshop")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("search").add_argument("spec")
    sub.add_parser("rerank").add_argument("resolutions")
    sub.add_parser("inspect").add_argument("urls", nargs="+")
    ip = sub.add_parser("index")
    ip.add_argument("--rebuild", nargs="?", const="", default=None)
    sub.add_parser("doctor")
    sub.add_parser("setup")
    sub.add_parser("contact").add_argument("vendor")
    wp = sub.add_parser("web")
    wp.add_argument("--port", type=int, default=8760)
    a = p.parse_args()
    fetch.VERBOSE = a.verbose
    if a.cmd == "search":
        search(a.spec)
    elif a.cmd == "rerank":
        rerank(a.resolutions)
    elif a.cmd == "inspect":
        inspect(a.urls)
    elif a.cmd == "index":
        rebuild_index(a.rebuild if a.rebuild is not None else "")
    elif a.cmd == "doctor":
        doctor()
    elif a.cmd == "setup":
        setup()
    elif a.cmd == "contact":
        contact(a.vendor)
    elif a.cmd == "web":
        from .web import serve
        serve(a.port)


if __name__ == "__main__":
    main()
