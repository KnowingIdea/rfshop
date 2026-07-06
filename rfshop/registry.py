"""Vendor registry: load vendors.yaml (package + user overlay), pick vendor subset for a category."""
import os
import pathlib
import yaml

DATA = pathlib.Path(os.environ.get("RFSHOP_HOME", "~/.rfshop")).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "outbox").mkdir(exist_ok=True)
VENDORS_FILE = pathlib.Path(__file__).resolve().parent / "vendors.yaml"
USER_VENDORS = DATA / "vendors.yaml"  # optional overlay: same schema, merged by name

# Category synonyms used both for vendor selection and URL/keyword filtering.
CATEGORY_SYNONYMS = {
    "lna": ["lna", "low-noise", "low noise", "cryogenic amplifier", "cryo amplifier", "amplifier"],
    "amplifier": ["amplifier", "amp", "gain block"],
    "attenuator": ["attenuator", "atten"],
    "termination": ["termination", "load", "terminator"],
    "circulator": ["circulator"],
    "isolator": ["isolator"],
    "cable": ["cable", "coax", "semi-rigid", "cryostat wiring"],
    "connector": ["connector"],
    "adapter": ["adapter", "adaptor"],
    "feedthrough": ["feedthrough", "feed-through", "hermetic", "bulkhead"],
    "filter": ["filter", "low pass", "high pass", "band pass", "lowpass", "highpass", "bandpass"],
    "mixer": ["mixer"],
    "coupler": ["coupler", "directional"],
    "switch": ["switch"],
    "dc_block": ["dc block", "dc-block", "inner/outer"],
    "bias_tee": ["bias tee", "bias-tee", "biastee"],
    "detector": ["detector"],
}


def load_vendors():
    vendors = yaml.safe_load(VENDORS_FILE.read_text())
    if USER_VENDORS.exists():
        by_name = {v["name"]: v for v in vendors}
        for v in yaml.safe_load(USER_VENDORS.read_text()) or []:
            by_name[v["name"]] = v  # user entry replaces or adds
        vendors = list(by_name.values())
    return vendors


def vendors_for(category):
    return [v for v in load_vendors() if category in v.get("categories", [])]


def synonyms(category):
    return CATEGORY_SYNONYMS.get(category, [category])
