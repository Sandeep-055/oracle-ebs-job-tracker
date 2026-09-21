#!/usr/bin/env python3
"""Daily Oracle EBS India job scan (runs in GitHub Actions).

Pipeline: collect -> normalize -> filter -> de-duplicate -> merge into data/jobs.json -> re-verify
jobs that were not listed today -> write data/status.json.

Rules this script follows (see README):
  * Nothing is invented. Every field comes from a source response; missing means missing.
  * A posting date must come from the source. No date -> the job is flagged and hidden by the site.
  * "active" needs evidence: listed by a source in the last few scans, or an official page that
    still loads without closure markers.
  * Sources that need credentials but have none are reported as "skipped"; portals that forbid
    automated access are reported as "unavailable". Nothing bypasses logins, CAPTCHAs or robots.txt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
CONFIG_PATH = os.environ.get("SOURCES_CONFIG", os.path.join(ROOT, "config", "sources.json"))

FRESH_DAYS = int(os.environ.get("FRESH_DAYS", "10"))          # posted within this many days
MAX_EXP = float(os.environ.get("MAX_EXP", "2"))                # minimum experience asked must be <= this
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "2"))            # still "active" if listed within this many days
KEEP_HISTORY_DAYS = 30
MAX_URL_CHECKS = int(os.environ.get("MAX_URL_CHECKS", "40"))
JSEARCH_QUERY_LIMIT = int(os.environ.get("JSEARCH_QUERY_LIMIT", "2"))
IST = timezone(timedelta(hours=5, minutes=30))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
UA = f"EBSJobTrackerBot/2.0 (+https://github.com/{REPO})" if REPO else "EBSJobTrackerBot/2.0"

QUERIES = [
    "Oracle EBS SCM", "Oracle EBS Finance", "Oracle EBS Functional Consultant",
    "Oracle EBS SCM Functional Consultant", "Oracle EBS Finance Functional Consultant",
    "Oracle EBS Support", "Oracle EBS Associate Consultant", "Oracle EBS Junior Consultant",
    "Oracle EBS Business Analyst", "Oracle Apps R12 Functional",
]
JSEARCH_QUERIES = ["Oracle EBS functional consultant", "Oracle EBS SCM finance support"]

# ───────────────────────── Oracle EBS vocabulary ─────────────────────────
# (code, label, category, name patterns (case-insensitive), short-code patterns (case-sensitive))
MODULES = [
    ("PO", "Purchasing", "SCM", [r"\bpurchasing\b", r"\bpurchase orders?\b"], [r"\bPO\b"]),
    ("INV", "Inventory", "SCM", [r"\binventory\b"], [r"\bINV\b"]),
    ("OM", "Order Management", "SCM", [r"\border management\b"], [r"\bOM\b"]),
    ("SOURCING", "Sourcing", "SCM", [r"\boracle sourcing\b", r"\bsourcing\b", r"\bsupplier negotiation"], []),
    ("IPROC", "iProcurement", "SCM", [r"\bi-?procurement\b"], []),
    ("WMS", "Warehouse Management", "SCM", [r"\bwarehouse management\b"], [r"\bWMS\b"]),
    ("BOM", "Bills of Material", "SCM", [r"\bbills? of materials?\b"], [r"\bBOM\b"]),
    ("SHIPPING", "Shipping Execution", "SCM", [r"\bshipping execution\b"], []),
    ("ASCP", "Supply Chain Planning", "SCM", [r"\badvanced supply chain planning\b"], [r"\bASCP\b"]),
    ("AP", "Payables", "Finance", [r"\baccounts payables?\b", r"\bpayables\b"], [r"\bAP\b"]),
    ("AR", "Receivables", "Finance", [r"\baccounts receivables?\b", r"\breceivables\b"], [r"\bAR\b"]),
    ("GL", "General Ledger", "Finance", [r"\bgeneral ledger\b"], [r"\bGL\b"]),
    ("FA", "Fixed Assets", "Finance", [r"\bfixed assets?\b"], [r"\bFA\b"]),
    ("CM", "Cash Management", "Finance", [r"\bcash management\b"], [r"\bCM\b"]),
    ("TAX", "GST/TDS/EBTax (India localization)", "Finance",
     [r"\be-?b-?tax\b", r"\bIndia localization\b"], [r"\bGST\b", r"\bTDS\b", r"\bJAI\b"]),
    ("AME", "Approval Management Engine", "Other", [r"\bapproval management engine\b"], [r"\bAME\b"]),
]
PROCESS = {
    "P2P": ([r"\bp2p\b", r"\bprocure[- ]to[- ]pay\b"], ["PO", "IPROC", "SOURCING", "AP"]),
    "O2C": ([r"\bo2c\b", r"\border[- ]to[- ]cash\b"], ["OM", "AR", "SHIPPING"]),
}


def has_ebs(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\be-?business\s+suite\b", text, re.I) or re.search(r"\boracle\s+(apps|applications)\b", text, re.I):
        return True
    oracle = re.search(r"\boracle\b", text, re.I)
    return bool(oracle and (re.search(r"\bebs\b", text, re.I) or re.search(r"\br12(\.\d+){0,2}\b", text, re.I)))


def ebs_count(text: str) -> int:
    return len(re.findall(r"\b(ebs|e-?business suite|oracle apps|r12(?:\.\d+){0,2})\b", text, re.I))


def detect_modules(text: str, allow_short: bool) -> tuple[list[str], list[str]]:
    found: set[str] = set()
    for code, _label, _cat, names, shorts in MODULES:
        if any(re.search(p, text, re.I) for p in names) or (allow_short and any(re.search(p, text) for p in shorts)):
            found.add(code)
    for tag, (pats, from_mods) in PROCESS.items():
        if any(re.search(p, text, re.I) for p in pats) or any(m in found for m in from_mods):
            found.add(tag)
    cats: set[str] = set()
    for code in found:
        cat = next((m[2] for m in MODULES if m[0] == code), None)
        if cat in ("SCM", "Finance"):
            cats.add(cat)
        if code in ("P2P", "O2C"):
            cats.add("SCM")
    if re.search(r"\bsupply chain\b|\bscm\b", text, re.I):
        cats.add("SCM")
    if re.search(r"\bfinancials?\b|\bfinance\b|\baccounting\b", text, re.I):
        cats.add("Finance")
    return sorted(found), sorted(cats)


# ───────────────────────── location / experience / work mode ─────────────────────────
CITY = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "bombay": "Mumbai", "mumbai": "Mumbai", "navi mumbai": "Navi Mumbai",
    "thane": "Thane", "gurgaon": "Gurugram", "gurugram": "Gurugram", "madras": "Chennai", "chennai": "Chennai",
    "calcutta": "Kolkata", "kolkata": "Kolkata", "hyderabad": "Hyderabad", "secunderabad": "Hyderabad", "pune": "Pune",
    "noida": "Noida", "greater noida": "Noida", "ghaziabad": "Ghaziabad", "faridabad": "Faridabad", "delhi": "Delhi",
    "new delhi": "Delhi", "delhi ncr": "Delhi", "ahmedabad": "Ahmedabad", "vadodara": "Vadodara", "surat": "Surat",
    "gandhinagar": "Gandhinagar", "kochi": "Kochi", "cochin": "Kochi", "thiruvananthapuram": "Thiruvananthapuram",
    "trivandrum": "Thiruvananthapuram", "coimbatore": "Coimbatore", "madurai": "Madurai", "jaipur": "Jaipur",
    "indore": "Indore", "bhopal": "Bhopal", "nagpur": "Nagpur", "chandigarh": "Chandigarh", "mohali": "Mohali",
    "lucknow": "Lucknow", "kanpur": "Kanpur", "bhubaneswar": "Bhubaneswar", "visakhapatnam": "Visakhapatnam",
    "vizag": "Visakhapatnam", "vijayawada": "Vijayawada", "mysore": "Mysuru", "mysuru": "Mysuru", "mangalore": "Mangaluru",
    "mangaluru": "Mangaluru", "patna": "Patna", "ranchi": "Ranchi", "raipur": "Raipur", "dehradun": "Dehradun", "goa": "Goa",
}
STATES = [
    "andhra pradesh", "telangana", "karnataka", "tamil nadu", "kerala", "maharashtra", "gujarat", "rajasthan", "delhi",
    "haryana", "uttar pradesh", "west bengal", "madhya pradesh", "odisha", "punjab", "bihar", "jharkhand", "chhattisgarh",
    "uttarakhand", "goa", "chandigarh",
]
FOREIGN = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|united kingdom|uk|england|canada|australia|singapore|uae|dubai|abu dhabi|saudi|qatar|"
    r"kuwait|oman|bahrain|germany|netherlands|ireland|philippines|malaysia|south africa|nigeria|kenya|new zealand|japan|"
    r"china|hong kong)\b", re.I)


def is_india(location: str | None, allow_unknown: bool) -> bool:
    if not location:
        return allow_unknown
    low = location.lower()
    if re.search(r"\bindia\b", low) or any(re.search(rf"\b{re.escape(c)}\b", low) for c in CITY) or any(s in low for s in STATES):
        return True
    if FOREIGN.search(low):
        return False
    return allow_unknown


def parse_location(raw: str | None) -> tuple[str | None, str | None, str | None]:
    loc = re.sub(r"\s+", " ", raw or "").strip() or None
    if not loc:
        return None, None, None
    parts = [re.sub(r"\bindia\b", "", p, flags=re.I).strip() for p in re.split(r"[,|/;]| - ", loc)]
    parts = [p for p in parts if p]
    city = state = None
    for p in parts:
        k = p.lower()
        if not city and k in CITY:
            city = CITY[k]
        if not state and k in STATES:
            state = p.title()
    if not city and parts and parts[0].lower() not in STATES and parts[0].lower() != "remote":
        city = parts[0].title()
    return loc, city, state


def parse_experience(text: str | None) -> tuple[float | None, float | None, str | None]:
    """Return (min_years, max_years, matched_text). max None means open-ended ("3+ years")."""
    if not text:
        return None, None, None
    t = text[:20000]
    cands: list[tuple[float, float | None, str, int]] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?|months?)", t, re.I):
        k = 1 / 12 if m.group(3).lower().startswith("month") else 1
        cands.append((float(m.group(1)) * k, float(m.group(2)) * k, m.group(0), m.start()))
    for m in re.finditer(
            r"(?:minimum|min\.?|at least)?\s*(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?|months?)(?:\s+of)?\s+"
            r"(?:relevant\s+|hands-on\s+|functional\s+|total\s+)?(?:experience|exp)", t, re.I):
        k = 1 / 12 if m.group(2).lower().startswith("month") else 1
        plus = bool(re.search(r"\+|minimum|min|at least", m.group(0), re.I))
        cands.append((float(m.group(1)) * k, None if plus else float(m.group(1)) * k, m.group(0).strip(), m.start()))
    for m in re.finditer(r"\b(freshers?|entry[- ]level|graduate trainee|no experience (?:required|needed))\b", t, re.I):
        cands.append((0.0, 1.0, m.group(0), m.start()))
    if not cands:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", t, re.I)
        if m:
            cands.append((float(m.group(1)), float(m.group(2)), m.group(0), m.start()))
    if not cands:
        return None, None, None
    pick = next((c for c in cands if re.search(r"exp", t[max(0, c[3] - 60): c[3] + len(c[2]) + 60], re.I)), cands[0])
    rnd = lambda x: None if x is None else round(x * 10) / 10  # noqa: E731
    return rnd(pick[0]), rnd(pick[1]), pick[2]


def detect_work_mode(hint: str | None, title: str, location: str | None, description: str) -> str:
    h = (hint or "").lower()
    if "hybrid" in h:
        return "hybrid"
    if re.search(r"remote|telecommute|work from home|wfh", h):
        return "remote"
    if re.search(r"on-?site|office", h):
        return "onsite"
    head = f"{title} {location or ''}".lower()
    if re.search(r"\bhybrid\b", head):
        return "hybrid"
    if re.search(r"\bremote\b|work from home|\bwfh\b", head):
        return "remote"
    d = description[:6000].lower()
    if re.search(r"\bhybrid (work|model|mode|role|setup)\b", d):
        return "hybrid"
    if re.search(r"\bfully remote\b|\bremote (work|role|position|job|opportunity)\b|\bwork(ing)? remotely\b|\bwork from home\b", d):
        return "remote"
    if re.search(r"\bwork from office\b|\bwfo\b|\bon-?site\b|\bonsite\b", d):
        return "onsite"
    return "unknown"


COMPANY_SUFFIX = re.compile(r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation|co|india|opc)\b")


def norm_company(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and "))
    return re.sub(r"\s+", " ", COMPANY_SUFFIX.sub(" ", n)).strip()


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def html_to_text(html: str) -> str:
    s = html or ""
    if "&lt;" in s and "<" not in s:  # Greenhouse ships escaped HTML
        s = unescape(s)
    if "<" in s and ">" in s:
        soup = BeautifulSoup(s, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        s = soup.get_text("\n")
    s = unescape(s)
    s = re.sub(r"[ \t\f\v\u00a0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r" *\n *", "\n", s)).strip()


# ───────────────────────── dates ─────────────────────────
def to_ist_date(value, today: date | None = None) -> str | None:
    """ISO string / date string / epoch (s or ms) -> 'YYYY-MM-DD' in IST. None if invalid or in the future."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        else:
            s = str(value).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                d = date.fromisoformat(s)
                return d.isoformat() if (today is None or d <= today) else None
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
        d = dt.astimezone(IST).date()
    except (ValueError, OverflowError, OSError):
        return None
    if d.year < 2000 or (today is not None and d > today):
        return None
    return d.isoformat()


def age_days(iso_date: str, today: date) -> int:
    return (today - date.fromisoformat(iso_date)).days


# ───────────────────────── HTTP (polite, never bypasses) ─────────────────────────
class HttpError(Exception):
    def __init__(self, status: int, host: str):
        super().__init__(f"HTTP {status} from {host}")
        self.status = status


class Blocked(Exception):
    """Login wall, CAPTCHA, rate limit or robots.txt refusal. We stop; we never work around it."""


_last_call: dict[str, float] = {}
_robots: dict[str, robotparser.RobotFileParser | str] = {}
SECRET_VALUES: list[str] = []


def redact(msg: str) -> str:
    """Remove API keys from anything that may be written to the public status file."""
    out = re.sub(r"(app_key|app_id|api_key|apikey|key|token|x-rapidapi-key)=([^&\s'\"]+)", r"\1=***", str(msg), flags=re.I)
    for s in SECRET_VALUES:
        if s and len(s) > 3:
            out = out.replace(s, "***")
    return out


def _throttle(host: str, gap: float = 0.7):
    wait = _last_call.get(host, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.time()


def http_get(url: str, *, params=None, headers=None, timeout=20, robots=False) -> requests.Response:
    host = urlparse(url).netloc
    if robots and not robots_allows(url):
        raise Blocked(f"robots.txt disallows fetching this page on {host}")
    last: Exception | None = None
    for attempt in range(3):
        _throttle(host)
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json,text/html;q=0.9,*/*;q=0.5", **(headers or {})}, timeout=timeout)
        except requests.RequestException as e:
            last = RuntimeError(f"network error contacting {host}: {type(e).__name__}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 429:
            raise Blocked(f"rate limited by {host} (429)")
        if r.status_code in (401, 403):
            raise Blocked(f"access restricted by {host} ({r.status_code})")
        if r.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        return r
    raise last or RuntimeError("request failed")


def robots_allows(url: str) -> bool:
    u = urlparse(url)
    origin = f"{u.scheme}://{u.netloc}"
    if origin not in _robots:
        try:
            _throttle(u.netloc)
            r = requests.get(f"{origin}/robots.txt", headers={"User-Agent": UA}, timeout=8)
            if r.status_code >= 500:
                _robots[origin] = "deny"
            elif r.status_code >= 400:
                _robots[origin] = "allow"
            else:
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                _robots[origin] = rp
        except requests.RequestException:
            _robots[origin] = "deny"  # cannot tell -> be conservative
    rp = _robots[origin]
    if rp == "allow":
        return True
    if rp == "deny":
        return False
    return rp.can_fetch(UA, url)  # type: ignore[union-attr]


CAPTCHA = re.compile(r"captcha|are you (a )?(robot|human)|unusual traffic|verify you are human", re.I)


def fetch_page(url: str) -> tuple[int, str]:
    r = http_get(url, robots=True)
    text = r.text[:1_500_000]
    if CAPTCHA.search(text[:4000]) and len(text) < 30000:
        raise Blocked(f"bot-challenge page on {urlparse(url).netloc}")
    return r.status_code, text


# ───────────────────────── JSON-LD (schema.org JobPosting) ─────────────────────────
def _collect(node, out: list) -> None:
    if isinstance(node, list):
        for n in node:
            _collect(n, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            out.append(node)
        if "@graph" in node:
            _collect(node["@graph"], out)


def extract_job_postings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            _collect(json.loads(tag.string or tag.get_text() or ""), found)
        except (ValueError, TypeError):
            continue
    out = []
    for j in found:
        locs = j.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        parts = []
        for l in locs:
            a = (l or {}).get("address", l) or {}
            if isinstance(a, dict):
                country = a.get("addressCountry")
                country = country.get("name") if isinstance(country, dict) else country
                bits = [a.get("addressLocality"), a.get("addressRegion"), country]
                parts.append(", ".join(str(b) for b in bits if b))
        org = j.get("hiringOrganization") or {}
        salary = ""
        v = ((j.get("baseSalary") or {}).get("value") or {}) if isinstance(j.get("baseSalary"), dict) else {}
        if isinstance(v, dict) and (v.get("minValue") or v.get("maxValue")):
            salary = f"{(j['baseSalary'].get('currency') or '')} {v.get('minValue', '')}-{v.get('maxValue', '')} (as listed)".strip()
        ident = j.get("identifier")
        out.append({
            "title": clean(j.get("title")), "company": clean(org.get("name") if isinstance(org, dict) else org),
            "location": " | ".join(parts), "description": html_to_text(str(j.get("description") or "")),
            "datePosted": j.get("datePosted"), "validThrough": j.get("validThrough"), "url": j.get("url"),
            "remote": str(j.get("jobLocationType", "")).upper() == "TELECOMMUTE", "salary": salary,
            "identifier": str(ident.get("value")) if isinstance(ident, dict) and ident.get("value") else None,
        })
    return [o for o in out if o["title"]]


# ───────────────────────── sources ─────────────────────────
PORTAL_HOSTS = re.compile(r"(linkedin|indeed|naukri|glassdoor|foundit|monster|shine|timesjobs|instahyre|apna|ziprecruiter|jooble|"
                          r"adzuna|talent\.com|simplyhired|careerjet|jobrapido|google)\.", re.I)
RESTRICTED = {
    "linkedin": "No public job-search API; automated collection is prohibited by LinkedIn's terms. Use the search links on the site, or JSearch.",
    "naukri": "No public job-search API; automated collection is prohibited by Naukri's terms. Use the search links on the site, or JSearch.",
    "indeed": "No self-serve public job-search API; automated collection is prohibited by Indeed's terms. Use the search links on the site, or JSearch.",
    "glassdoor": "Partner-only API; automated access is not available",
