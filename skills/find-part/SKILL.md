---
name: find-part
description: Find RF/microwave/cryogenic measurement parts from a natural-language spec ("cryo LNA 4-8 GHz, 4 K, 30+ dB"). Deep-scrapes a curated vendor registry, ranks EVERY option by criteria fit then price (near-misses tiered, not hidden), covers unscrapeable vendors via WebSearch, drafts RFQ emails. Use when the user asks to find, shop for, or compare RF parts, or invokes /find-part.
---

# find-part

Data dir: `~/.rfshop/` (results.md/json, flags.json, outbox/, caches). Token discipline: NEVER read raw vendor pages, cache.db, index.db, or full results.json — only CLI stdout, flags.json, and files you write.

## Step 0 — bootstrap (first run only)

`python3 -c "import rfshop"` — if it fails: `pip install -e <plugin root>` then `python3 -m playwright install chromium`. The plugin root is two directories above this skill's base directory (the base directory is shown when this skill loads; layout is `<root>/skills/find-part/`). Run commands via `python3 -m rfshop ...` from any directory.

## Steps

1. Parse the request into `~/.rfshop/spec.json`. Schema (omit unknown keys; put unstructured criteria like materials in `other` — they ARE scored):
```json
{"category": "lna|amplifier|attenuator|termination|circulator|isolator|cable|connector|adapter|feedthrough|filter|mixer|coupler|switch|dc_block|bias_tee|detector",
 "freq_ghz": [4, 8], "temp_k": 4, "gain_db_min": 30, "noise_temp_k_max": 5,
 "attenuation_db": 1, "connector": "SMA", "mount": "bulkhead",
 "max_lead_weeks": 4, "prefer_vendors": ["XMA"], "exclude_vendors": [],
 "other": ["BeCu inner", "non-magnetic"]}
```
Ask about or infer urgency: "need it this month" → `max_lead_weeks`. If the user has vendor preferences (or standing ones from memory), set `prefer_vendors` (boosted, marked ★) / `exclude_vendors` (dropped).

2. `python3 -m rfshop search ~/.rfshop/spec.json` (timeout 600s; warn the user the FIRST search per category deep-crawls vendors and can take minutes — cached 7 days after). Stdout = tiered markdown table: Tier A (all criteria met) → B (all checkable met, some unverified) → C (one miss) → D (2+ misses), each tier sorted by fit then price.

3. Backstop pass — the search output lists "Backstop vendors" that resist scraping (Digi-Key, Mini-Circuits, Times Microwave, ...). For the 2-4 most relevant to the category, WebSearch `site:<vendor-domain> <key terms>`; feed promising product URLs to `python3 -m rfshop inspect <url> <url> ...` which enriches and re-ranks them into the results.

4. If flags.json is non-empty (top results with unverified criteria): read it, infer values from each ≤300-char excerpt, write `~/.rfshop/resolutions.json` as `[{"url": "...", "specs": {"freq_ghz": [0,18], "bulkhead": true}}]` — only values you're confident of. Then `python3 -m rfshop rerank ~/.rfshop/resolutions.json`.

5. Present the final tiered table verbatim (stdout already trims to the top 10; state the total count and that the full list is in `~/.rfshop/results.md` — read it only if the user asks for more options). Note vendors that errored and any backstop vendors you couldn't cover. The **Lead** column: `stock`/`orderable` = page-level evidence; `~N wk` = stated lead time; `~N wk (vendor est)` = registry's typical estimate, not part-specific — say so; `custom` = made to order. When lead time matters to the user, flag that RFQ-vendor leads are only confirmed by asking — the RFQ draft already asks.

6. RFQ drafts on request: `python3 -m rfshop contact "<vendor>"` for the address, then write `~/.rfshop/outbox/<vendor>-<part>.txt`:
```
To: <rfq email>
Subject: Quote request — <part number>
<3-5 sentence body: part number, qty (use [qty] if user gave none), key specs to confirm, lead time question, user's affiliation.>
```
Never send — draft only. Tell the user the file paths.

## Maintenance

- `python3 -m rfshop doctor` — per-vendor health. `python3 -m rfshop index --rebuild [vendor]` — force reindex.
- Add vendors: `~/.rfshop/vendors.yaml` (same schema as packaged registry, merged by name; `strategy: sitemap|search|backstop`, `search_url` with `{q}`, `render: true` for JS sites).
- Too few results? Broaden: sibling category (`lna`→`amplifier`), drop a constraint, or WebSearch beyond the registry and `inspect` the URLs.
