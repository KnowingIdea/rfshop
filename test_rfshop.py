"""Self-checks: extractor, URL filter, criterion evaluation, tiering, rank order.
Run: python3 test_rfshop.py  (no network)"""
from rfshop.adapters import _url_ok
from rfshop.extract import parse_specs
from rfshop.rank import evaluate, rank, tier

# --- extractor ---
DS = ("LNF-LNC4_8C Cryogenic Low Noise Amplifier 4-8 GHz. Gain typically 42 dB. "
      "Average noise temperature 1.5 K at 5 K ambient. SMA connectors. $7,300.00")
s = parse_specs(DS)
assert s["freq_ghz"] == [4.0, 8.0] and s["gain_db"] == 42.0 and s["noise_k"] == 1.5
assert s["price_usd"] == 7300.0 and s["cryo"] and s["connector"] == "SMA"

s2 = parse_specs("Bulkhead cryogenic attenuator, attenuation 1 dB, DC to 26.5 GHz, 2.92mm")
assert s2["attenuation_db"] == 1.0 and s2["bulkhead"] and s2["freq_ghz"] == [0.0, 26.5]

assert parse_specs("filter 400 MHz to 800 MHz")["freq_ghz"] == [0.4, 0.8]
assert "price_usd" not in parse_specs("shipping $ 5 estimate")  # sub-$10 = page noise

# --- URL filter ---
toks = ["attenuator", "atten", "cryo"]
assert _url_ok("https://x.com/product/2082-6241-cryo/", toks, "attenuator")
assert not _url_ok("https://x.com/about-us/", toks, "attenuator")            # junk
assert not _url_ok("https://x.com/products/attenuators/", toks, "attenuator")  # listing
assert not _url_ok("https://x.com/product/cryo-isolator-4-8/", toks, "attenuator")  # wrong type
# cryoatt regression: index path filters with toks=[""] and must keep QMC bulkhead slugs
assert _url_ok("https://q.com/product/dc-to-18-ghz-10-db-attenuators-qmc-cryoatt-10blk-sma-bulkhead/",
               [""], "attenuator")

# --- evaluation / tiering ---
spec = {"category": "attenuator", "freq_ghz": [0, 18], "temp_k": 0.01,
        "attenuation_db": 1, "connector": "SMA", "mount": "bulkhead"}
full = evaluate({"title": "cryo attenuator", "url": "u1", "specs":
                 {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1,
                  "connector": "SMA", "bulkhead": True}}, spec)
assert not full["miss"] and not full["unknown"] and tier(full) == 0, full

unverified = evaluate({"title": "attenuator", "url": "u2", "specs":
                       {"freq_ghz": [0, 26.5], "attenuation_db": 1, "connector": "SMA",
                        "cryo": False, "bulkhead": False}}, spec)
assert not unverified["miss"] and set(unverified["unknown"]) == {"cryo", "bulkhead"}
assert tier(unverified) == 1

near = evaluate({"title": "attenuator", "url": "u3", "specs":
                 {"freq_ghz": [0, 12], "cryo": True, "attenuation_db": 1,
                  "connector": "SMA", "bulkhead": True}}, spec)
assert near["miss"] == ["freq(partial)"] and tier(near) == 2

# --- rank: keeps near-misses, orders by tier then price ---
cands = [
    {"url": "b", "title": "cryo attenuator B", "vendor": "V",
     "specs": {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "price_usd": 500}},
    {"url": "a", "title": "cryo attenuator A", "vendor": "V",
     "specs": {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "price_usd": 300}},
    {"url": "n", "title": "cryo attenuator narrow", "vendor": "V",
     "specs": {"freq_ghz": [0, 12], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "price_usd": 100}},
    {"url": "x", "title": "power divider", "vendor": "V", "specs": {}},
]
r = rank(cands, spec)
assert [c["url"] for c in r] == ["a", "b", "n"], [c["url"] for c in r]
assert r[2]["miss"] == ["freq(partial)"]  # near-miss kept, down-ranked

# lead time extraction
assert parse_specs("Lead time: 6-8 weeks ARO")["lead_weeks"] == 7.0
assert parse_specs("In Stock — ships today")["lead_weeks"] == 0.0
assert parse_specs("Add to cart $375")["lead_weeks"] == 0.5
assert parse_specs("This item is made to order")["lead_note"].startswith("custom")
assert "lead_weeks" not in parse_specs("This item is made to order")

# lead criterion + vendor prefer/exclude
spec_l = dict(spec, max_lead_weeks=4, prefer_vendors=["GoodCo"], exclude_vendors=["BadCo"])
lc = [
    {"url": "s", "title": "cryo attenuator", "vendor": "GoodCo",
     "specs": {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "lead_weeks": 0.0}},
    {"url": "slow", "title": "cryo attenuator", "vendor": "SlowCo",
     "specs": {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "lead_weeks": 12.0}},
    {"url": "bad", "title": "cryo attenuator", "vendor": "BadCo",
     "specs": {"freq_ghz": [0, 18], "cryo": True, "attenuation_db": 1, "connector": "SMA",
               "bulkhead": True, "lead_weeks": 0.0}},
]
rl = rank(lc, spec_l)
assert [c["url"] for c in rl] == ["s", "slow"], [c["url"] for c in rl]  # BadCo excluded
assert rl[0]["preferred"] and "lead" in rl[0]["met"] and rl[1]["miss"] == ["lead"]

# other[] keyword scoring
spec_kw = {"category": "cable", "other": ["becu", "stainless"]}
kwc = evaluate({"title": "SS coax", "url": "u", "specs": {},
                "kw": "semi-rigid cable becu center stainless outer"}, spec_kw)
assert set(kwc["met"]) == {"'becu'", "'stainless'"}, kwc["met"]

print("all checks pass")
