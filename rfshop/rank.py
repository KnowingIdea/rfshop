"""Per-criterion evaluation (met/miss/unknown), tiering, sort, markdown report.
Near-misses are kept and down-ranked, never silently dropped."""
from .registry import synonyms

SYSTEM_WORDS = ("system", "breadboard", "kit", "dewar", "chassis", "rack-mount")
TIERS = {0: "A — meets all stated criteria", 1: "B — meets all checkable criteria (some unverified)",
         2: "C — misses one criterion", 3: "D — misses two or more"}


def _overlap(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return max(0.0, hi - lo)


def _named(c, category):
    name = (c.get("title", "") + " " + c.get("url", "")).lower().replace("_", "-")
    return any(w.replace(" ", "-") in name or w.replace(" ", "") in name
               for w in synonyms(category))


def evaluate(c, spec):
    """Sets c['met'], c['miss'], c['unknown'] lists of criterion names."""
    s = c.get("specs", {})
    met, miss, unk = [], [], []

    def judge(name, value, ok):
        (unk if value is None else met if ok else miss).append(name)

    if spec.get("freq_ghz"):
        f = s.get("freq_ghz")
        if f is None:
            unk.append("freq")
        else:
            want = spec["freq_ghz"]
            cov = _overlap(want, f) / ((want[1] - want[0]) or 0.1)
            met.append("freq") if cov >= 0.99 else miss.append(
                "freq" if cov == 0 else "freq(partial)")
    if (spec.get("temp_k") or 300) <= 77:
        # regex absence isn't proof of room-temp-only: False -> unverified, not miss
        (met if s.get("cryo") else unk).append("cryo")
    if spec.get("gain_db_min"):
        judge("gain", s.get("gain_db"), (s.get("gain_db") or 0) >= spec["gain_db_min"])
    if spec.get("noise_temp_k_max"):
        judge("noise", s.get("noise_k"), (s.get("noise_k") or 1e9) <= spec["noise_temp_k_max"])
    if spec.get("attenuation_db") is not None:
        judge("atten", s.get("attenuation_db"),
              abs((s.get("attenuation_db") or 1e9) - spec["attenuation_db"]) <= 0.5)
    if spec.get("connector"):
        want = spec["connector"].upper().replace(" ", "")
        judge("connector", s.get("connector"), want in (s.get("connector") or ""))
    if spec.get("mount") == "bulkhead":
        (met if s.get("bulkhead") else unk).append("bulkhead")
    hay = ((c.get("kw") or "") + " " + c.get("title", "")).lower()
    for kw in spec.get("other") or []:
        k = kw.lower().strip()
        (met if k and k in hay else unk).append(f"'{k[:20]}'")
    c["met"], c["miss"], c["unknown"] = met, miss, unk
    return c


def desirability(c, spec):
    """Tie-break score within equal (miss, met) groups."""
    s = c.get("specs", {})
    sc = 1.5 if _named(c, spec["category"]) else 0.0
    if spec.get("freq_ghz") and s.get("freq_ghz"):
        want = spec["freq_ghz"]
        sc += 3 * min(_overlap(want, s["freq_ghz"]) / ((want[1] - want[0]) or 0.1), 1.0)
    if s.get("noise_k"):
        sc += max(0, 1 - s["noise_k"] / 20)
    if s.get("gain_db") and spec.get("gain_db_min"):
        sc += min((s["gain_db"] - spec["gain_db_min"]) / 20, 0.5)
    if s.get("price_usd"):
        sc += 0.5
    name = (c.get("title", "") + " " + c.get("url", "")).lower()
    if any(w in name for w in SYSTEM_WORDS):
        sc -= 2
    return round(sc, 2)


def tier(c):
    n = len(c["miss"])
    return 0 if n == 0 and not c["unknown"] else 1 if n == 0 else 2 if n == 1 else 3


def rank(cands, spec):
    kept = []
    for c in cands:
        if c.get("error"):
            continue
        evaluate(c, spec)
        if not c["met"] and not _named(c, spec["category"]):
            continue  # nothing verifiably relevant
        c["tier"] = tier(c)
        c["score"] = desirability(c, spec)
        kept.append(c)
    kept.sort(key=lambda c: (c["tier"], len(c["miss"]), -len(c["met"]), -c["score"],
                             c.get("specs", {}).get("price_usd") or 1e12))
    return kept


def _row(i, c):
    s = c.get("specs", {})
    f = f"{s['freq_ghz'][0]:g}–{s['freq_ghz'][1]:g}" if s.get("freq_ghz") else "?"
    ks = []
    if s.get("gain_db"): ks.append(f"{s['gain_db']:g} dB gain")
    if s.get("noise_k"): ks.append(f"{s['noise_k']:g} K noise")
    if s.get("attenuation_db"): ks.append(f"{s['attenuation_db']:g} dB atten")
    if s.get("connector"): ks.append(s["connector"])
    if s.get("cryo"): ks.append("cryo")
    if s.get("bulkhead"): ks.append("bulkhead")
    match = f"{len(c['met'])}✓"
    if c["unknown"]:
        match += f" {len(c['unknown'])}?"
    if c["miss"]:
        match += " ✗" + ",".join(c["miss"])
    price = f"${s['price_usd']:,.0f}" if s.get("price_usd") else "RFQ"
    return (f"| {i} | {c['title'][:60]} | {c['vendor']} | {match} | {f} | "
            f"{', '.join(ks) or '?'} | {price} | {c['url']} |")


def markdown(results, spec, errors):
    head = f"# Results: {spec.get('category')} " + (
        f"{spec['freq_ghz'][0]:g}–{spec['freq_ghz'][1]:g} GHz" if spec.get("freq_ghz") else "")
    lines, cur = [head], None
    for i, c in enumerate(results, 1):
        if c["tier"] != cur:
            cur = c["tier"]
            lines += ["", f"## Tier {TIERS[cur]}", "",
                      "| # | Part | Vendor | Match | Freq (GHz) | Key specs | Price | Link |",
                      "|---|------|--------|-------|-----------|-----------|-------|------|"]
        lines.append(_row(i, c))
    if errors:
        lines += ["", "Vendors with errors/no reach: " + ", ".join(sorted(errors))]
    return "\n".join(lines) + "\n"
