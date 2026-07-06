"""Local web UI: spec form -> tiered results table. stdlib only.
If the `claude` CLI is installed, adds a natural-language search box and
auto-resolves unverified specs after each search (subscription, no API key).
python -m rfshop web [--port 8760]"""
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import llm, rank
from .cli import rerank as cli_rerank
from .cli import search as cli_search
from .registry import CATEGORY_SYNONYMS, DATA, load_vendors

_search_lock = threading.Lock()  # one search at a time; playwright + politeness
SHOW = 10  # rows visible before "Show all"

PAGE = """<!doctype html><meta charset="utf-8">
<title>rfshop</title>
<style>
 body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1a1a2e}
 h1{font-size:1.4rem} form{display:grid;grid-template-columns:repeat(4,1fr);gap:.7rem;align-items:end;
   background:#f6f7fb;padding:1rem;border-radius:10px}
 label{font-size:.78rem;color:#555;display:block} input,select{width:100%;padding:.45rem;border:1px solid #ccd;
   border-radius:6px;font-size:.9rem;box-sizing:border-box}
 button{padding:.6rem;background:#4a4adf;color:#fff;border:0;border-radius:8px;
   font-size:1rem;cursor:pointer} button:disabled{opacity:.5}
 form>button{grid-column:span 4}
 table{border-collapse:collapse;width:100%;margin-top:1rem;font-size:.85rem}
 th,td{border-bottom:1px solid #eee;padding:.45rem .5rem;text-align:left;vertical-align:top}
 th{background:#f6f7fb} h2{font-size:1rem;margin:1.4rem 0 .2rem}
 .miss{color:#c0392b}.rfq{color:#888} a{color:#4a4adf} #status{margin:1rem 0;color:#555}
 .wrap{overflow-x:auto} .extra{display:none}
 #nl{display:none;background:#eef0ff;padding:1rem;border-radius:10px;margin-bottom:.8rem}
 #nl div{display:flex;gap:.6rem} #nl input{flex:1}
</style>
<h1>rfshop — RF/microwave/cryogenic parts search</h1>
<div id="nl">
 <label>Describe the part in plain language — Claude parses it and fills the form below</label>
 <div><input id="nlq" placeholder="cryo attenuator 1 dB bulkhead SMA, DC-18 GHz, need it within 6 weeks">
 <button id="nlgo">Search</button></div>
</div>
<form id="f">
 <div><label>Category</label><select name="category">CATEGORY_OPTIONS</select></div>
 <div><label>Freq low (GHz)</label><input name="flo" type="number" step="any" placeholder="4"></div>
 <div><label>Freq high (GHz)</label><input name="fhi" type="number" step="any" placeholder="8"></div>
 <div><label>Operating temp (K)</label><input name="temp" type="number" step="any" placeholder="4"></div>
 <div><label>Min gain (dB)</label><input name="gain" type="number" step="any"></div>
 <div><label>Max noise temp (K)</label><input name="noise" type="number" step="any"></div>
 <div><label>Attenuation (dB)</label><input name="atten" type="number" step="any"></div>
 <div><label>Connector</label><input name="conn" placeholder="SMA"></div>
 <div><label>Mount</label><select name="mount"><option value="">any</option><option>bulkhead</option></select></div>
 <div><label>Max lead time (weeks)</label><input name="lead" type="number" step="any" placeholder="any"></div>
 <div><label>Prefer vendors (comma-sep)</label><input name="prefer" placeholder="XMA, Quantum Microwave"></div>
 <div><label>Exclude vendors</label><input name="exclude" placeholder=""></div>
 <div style="grid-column:span 3"><label>Other keywords (comma-sep)</label>
   <input name="other" placeholder="BeCu, stainless, non-magnetic"></div>
 <button>Search (first run per category crawls vendors — minutes)</button>
</form>
<div id="status"></div><div id="out" class="wrap"></div>
<script>
const f=document.getElementById('f'),st=document.getElementById('status'),out=document.getElementById('out');
const LLM=LLM_FLAG; if(LLM)document.getElementById('nl').style.display='block';
function fill(s){const set=(n,v)=>{if(f.elements[n])f.elements[n].value=v==null?'':v};
 set('category',s.category);set('flo',s.freq_ghz?s.freq_ghz[0]:'');set('fhi',s.freq_ghz?s.freq_ghz[1]:'');
 set('temp',s.temp_k);set('gain',s.gain_db_min);set('noise',s.noise_temp_k_max);set('atten',s.attenuation_db);
 set('conn',s.connector||'');set('mount',s.mount||'');set('lead',s.max_lead_weeks);
 set('prefer',(s.prefer_vendors||[]).join(', '));set('exclude',(s.exclude_vendors||[]).join(', '));
 set('other',(s.other||[]).join(', '));}
async function run(body){
 const btns=document.querySelectorAll('button');btns.forEach(b=>b.disabled=true);
 st.textContent='Searching… (deep-crawling uncached vendors can take several minutes)';out.innerHTML='';
 try{const r=await fetch('/search',{method:'POST',body:JSON.stringify(body)});
   const j=await r.json();
   if(j.error){st.textContent=j.error;return}
   if(j.spec)fill(j.spec);
   st.textContent=j.msg; out.innerHTML=j.html;
   if(out.querySelector('.extra')){const b=document.createElement('button');
     b.textContent='Show all '+j.n+' options';b.style.margin='0.6rem 0';
     b.onclick=()=>{out.querySelectorAll('.extra').forEach(e=>e.classList.remove('extra'));b.remove()};
     out.appendChild(b);}}
 catch(err){st.textContent='Error: '+err;}
 finally{btns.forEach(b=>b.disabled=false);}}
f.onsubmit=e=>{e.preventDefault();run(Object.fromEntries(new FormData(f)));};
document.getElementById('nlgo').onclick=()=>{const q=document.getElementById('nlq').value.trim();
 if(q)run({nl:q});};
</script>"""


def _spec_from_form(d):
    spec = {"category": d["category"], "other": []}
    if d.get("flo") and d.get("fhi"):
        spec["freq_ghz"] = [float(d["flo"]), float(d["fhi"])]
    for k, key in [("temp", "temp_k"), ("gain", "gain_db_min"),
                   ("noise", "noise_temp_k_max"), ("atten", "attenuation_db")]:
        if d.get(k):
            spec[key] = float(d[k])
    if d.get("conn"):
        spec["connector"] = d["conn"]
    if d.get("mount"):
        spec["mount"] = d["mount"]
    if d.get("lead"):
        spec["max_lead_weeks"] = float(d["lead"])
    for k, key in [("prefer", "prefer_vendors"), ("exclude", "exclude_vendors"),
                   ("other", "other")]:
        if d.get(k):
            spec[key] = [w.strip() for w in d[k].split(",") if w.strip()]
    return spec


def _html_results():
    data = json.loads((DATA / "results.json").read_text())
    rfq = {v["name"]: v.get("rfq_email") for v in load_vendors()}
    out, cur = [], None
    for i, c in enumerate(data["results"]):
        extra = " class=extra" if i >= SHOW else ""
        if c["tier"] != cur:
            cur = c["tier"]
            out.append(f"</table><h2{extra}>Tier {html.escape(rank.TIERS[cur])}</h2>"
                       f"<table{extra}><tr><th>Part</th><th>Vendor</th><th>Match</th>"
                       "<th>Freq (GHz)</th><th>Specs</th><th>Price</th><th>Lead</th><th>RFQ</th></tr>")
        s = c.get("specs", {})
        f = f"{s['freq_ghz'][0]:g}–{s['freq_ghz'][1]:g}" if s.get("freq_ghz") else "?"
        ks = ", ".join(filter(None, [
            f"{s['gain_db']:g} dB gain" if s.get("gain_db") else "",
            f"{s['noise_k']:g} K noise" if s.get("noise_k") else "",
            f"{s['attenuation_db']:g} dB atten" if s.get("attenuation_db") else "",
            s.get("connector") or "", "cryo" if s.get("cryo") else "",
            "bulkhead" if s.get("bulkhead") else ""]))
        match = f"{len(c['met'])}✓ {len(c['unknown'])}?"
        if c["miss"]:
            match += f" <span class=miss>✗{html.escape(','.join(c['miss']))}</span>"
        price = f"${s['price_usd']:,.0f}" if s.get("price_usd") else "<span class=rfq>RFQ</span>"
        mail = rfq.get(c["vendor"]) or ""
        star = "★ " if c.get("preferred") else ""
        out.append(f"<tr{extra}><td>{star}<a href='{html.escape(c['url'])}' target=_blank>"
                   f"{html.escape(c['title'][:70])}</a></td><td>{html.escape(c['vendor'])}</td>"
                   f"<td>{match}</td><td>{f}</td><td>{html.escape(ks) or '?'}</td><td>{price}</td>"
                   f"<td>{html.escape(rank.lead_str(c))}</td><td>{html.escape(mail)}</td></tr>")
    out.append("</table>")
    return "".join(out), len(data["results"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html"):
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, obj):
        self._send(json.dumps(obj), "application/json")

    def do_GET(self):
        cats = "".join(f"<option>{c}</option>" for c in CATEGORY_SYNONYMS)
        self._send(PAGE.replace("CATEGORY_OPTIONS", cats)
                       .replace("LLM_FLAG", "true" if llm.available() else "false"))

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if d.get("nl"):
            spec = llm.nl_to_spec(d["nl"])
            if not spec:
                self._json({"error": "Claude could not parse that request — "
                            "try the form below, or check that `claude -p hi` works."})
                return
        else:
            spec = _spec_from_form(d)
        sp = DATA / "web_spec.json"
        sp.write_text(json.dumps(spec))
        note = ""
        with _search_lock:
            try:
                cli_search(str(sp))
                if llm.available():
                    n = llm.resolve_flags()
                    if n:
                        cli_rerank(str(DATA / "resolutions.json"))
                        note = f" — {n} unverified specs resolved by Claude"
            except SystemExit as e:
                self._json({"error": str(e)})
                return
        tbl, n = _html_results()
        self._json({"n": n, "html": tbl, "spec": spec,
                    "msg": f"{n} options — best {min(n, SHOW)} shown, "
                           f"ranked by criteria fit then price{note}"})


def serve(port=8760):
    print(f"rfshop web UI: http://localhost:{port}"
          + ("  (Claude NL search: on)" if llm.available() else "  (claude CLI not found — form only)"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
