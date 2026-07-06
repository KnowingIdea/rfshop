"""Optional LLM layer via local `claude -p` (Claude Code subscription, no API key).
Used by the web UI for natural-language specs + resolving unverified extractions."""
import json
import re
import shutil
import subprocess

from .registry import DATA

SPEC_SCHEMA = """{"category": "lna|amplifier|attenuator|termination|circulator|isolator|cable|connector|adapter|feedthrough|filter|mixer|coupler|switch|dc_block|bias_tee|detector",
 "freq_ghz": [4, 8], "temp_k": 4, "gain_db_min": 30, "noise_temp_k_max": 5,
 "attenuation_db": 1, "connector": "SMA", "mount": "bulkhead",
 "max_lead_weeks": 4, "prefer_vendors": ["XMA"], "exclude_vendors": [],
 "other": ["BeCu inner", "non-magnetic"]}"""


def available():
    return shutil.which("claude") is not None


def ask(prompt, timeout=180):
    # ponytail: headless -p call per request, no session reuse
    try:
        r = subprocess.run(["claude", "-p", prompt, "--output-format", "text"],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _json_from(reply):
    if not reply:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", reply, re.S) or re.search(r"[\[{].*[\]}]", reply, re.S)
    try:
        return json.loads(m.group(1) if m.lastindex else m.group(0)) if m else None
    except Exception:
        return None


def nl_to_spec(text):
    """Natural-language part request -> spec dict, or None."""
    spec = _json_from(ask(
        "Convert this RF/microwave part request into a JSON spec. Schema (omit unknown keys; "
        "put unstructured criteria like materials in \"other\"):\n" + SPEC_SCHEMA +
        "\n\nRequest: " + text + "\n\nReply with ONLY the JSON object."))
    return spec if isinstance(spec, dict) and spec.get("category") else None


def resolve_flags():
    """flags.json excerpts -> resolutions.json via LLM. Returns count resolved (0 if none/fail)."""
    fpath = DATA / "flags.json"
    if not fpath.exists():
        return 0
    flags = json.loads(fpath.read_text())
    if not flags:
        return 0
    res = _json_from(ask(
        "Each item is an RF part page with unverified specs and a text excerpt. From each "
        "excerpt infer ONLY values you are confident of (keys: freq_ghz [lo,hi] GHz, gain_db, "
        "noise_k, attenuation_db, connector, cryo, bulkhead, lead_weeks). Skip hopeless items.\n"
        "Reply with ONLY a JSON array: [{\"url\": \"...\", \"specs\": {...}}]\n\n"
        + json.dumps(flags[:20])))
    if not isinstance(res, list) or not res:
        return 0
    res = [r for r in res if isinstance(r, dict) and r.get("url") and isinstance(r.get("specs"), dict)]
    (DATA / "resolutions.json").write_text(json.dumps(res))
    return len(res)
