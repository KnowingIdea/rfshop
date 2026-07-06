# rfshop — RF / microwave / cryogenic parts shopper

Deep-scrapes a curated registry of ~50 RF/microwave/cryogenic component vendors (Low Noise Factory, XMA, Quantum Microwave, CryoCoax, Marki, Mini-Circuits, …), extracts specs from product pages and PDF datasheets, and ranks **every** option by how well it matches your criteria — near-misses are tiered and shown, never hidden — then by price. Drafts concise RFQ emails for quote-only vendors.

Built for dilution-refrigerator / quantum-measurement shopping: cryogenic LNAs, attenuators, circulators, semi-rigid cabling, hermetic feedthroughs, and the rest of the microwave chain.

## Install

```bash
pip install -e .
python -m playwright install chromium   # for JS-heavy / anti-bot vendor sites
```

## Use as a Claude Code plugin (natural-language queries)

```
/plugin marketplace add <you>/rfshop
/plugin install rfshop@rfshop
```

Then: `/find-part "cryogenic low-noise amplifier 4-8 GHz rated for 4 K, 30+ dB gain"`.
Claude parses the query, runs the scraper, verifies ambiguous extractions, covers bot-walled vendors via web search, and drafts RFQ emails on request. No API key — your Claude Code subscription is the LLM.

## Use without Claude (web UI or CLI)

```bash
rfshop web                 # form UI at http://localhost:8760
rfshop search spec.json    # spec.json: {"category":"attenuator","freq_ghz":[0,18],"temp_k":0.01,...}
rfshop doctor              # vendor registry health
rfshop index --rebuild     # force reindex (normally auto, 7-day cache)
rfshop contact "XMA"       # RFQ email address
```

Results land in `~/.rfshop/results.md` (tiered markdown table).

## How it ranks

Each stated criterion is judged per part: **met / missed / unverified** (a page that doesn't mention cryo rating is *unverified*, not failed). Tiers: **A** all met · **B** all checkable met · **C** one miss · **D** more. Within a tier: match fraction → band coverage/noise/etc. → price ascending. `RFQ` = quote-only vendor.

## Vendor registry

`rfshop/vendors.yaml` ships ~50 vendors. Extend/override in `~/.rfshop/vendors.yaml` (merged by name):

```yaml
- name: Some Vendor
  url: https://vendor.com
  categories: [attenuator, termination]
  strategy: sitemap          # sitemap | search | backstop
  # search_url: https://vendor.com/search?q={q}   # for strategy: search
  # render: true                                  # JS-heavy site
  price_listed: false
  rfq_email: sales@vendor.com
```

`backstop` marks vendors that resist scraping (bot walls); the Claude skill covers them via web search + `rfshop inspect <url>`.

## Notes

- First search per category deep-crawls vendor catalogs (minutes); everything is cached 7 days in `~/.rfshop/`.
- Scraping is polite: per-domain rate limiting, robots-declared sitemaps, no login walls.
- **Always verify specs against the manufacturer datasheet before purchasing** — extraction is regex-based and vendors change pages.
```
python test_rfshop.py   # offline self-checks
```
