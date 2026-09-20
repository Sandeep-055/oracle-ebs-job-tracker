import json, re, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse
import requests
from bs4 import BeautifulSoup

DATA = "data/jobs.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OracleEBSJobTracker/1.0; +https://github.com/)"}
TODAY = datetime.now(timezone.utc).date()
CUTOFF = TODAY - timedelta(days=10)

QUERIES = [
    '"Oracle EBS" fresher India jobs',
    '"Oracle EBS" "0-2 years" India jobs',
    '"Oracle EBS" "1-2 years" India jobs',
    '"Oracle EBS" "Associate Consultant" India',
    '"Oracle EBS" "Junior Consultant" India',
    '"Oracle EBS" SCM fresher India',
    '"Oracle EBS" Finance fresher India',
    '"Oracle EBS" support "0-2" India',
    '"Oracle EBS" "functional consultant" India',
]

ROLE_TERMS = [
    "oracle ebs", "oracle apps", "ebs r12", "oracle applications",
]
AREA_TERMS = [
    "scm", "supply chain", "procurement", "inventory", "purchasing",
    "order management", "finance", "functional", "support", "consultant",
    "business analyst", "associate consultant", "junior consultant",
]
ENTRY_TERMS = [
    "fresher", "freshers", "0-1", "0–1", "0-2", "0–2",
    "1-2", "1–2", "entry level", "entry-level", "graduate",
    "0 to 2", "1 to 2",
]
INDIA_TERMS = [
    "india", "hyderabad", "bangalore", "bengaluru", "chennai",
    "pune", "mumbai", "delhi", "noida", "gurugram", "gurgaon",
    "kolkata", "coimbatore", "jaipur", "ahmedabad", "indore",
]

def load():
    try:
        with open(DATA, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save(rows):
    rows.sort(key=lambda x: (x.get("posted_date") or "", x.get("match_score", 0)), reverse=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def search_ddg(query):
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a.result__a"):
        href = a.get("href")
        title = clean(a.get_text(" ", strip=True))
        parent = a.find_parent("div", class_="result")
        snippet = clean(parent.get_text(" ", strip=True) if parent else "")
        if href and title:
            out.append((title, href, snippet))
    return out

def extract_date(text):
    # Conservative: only accept explicit recent dates in common forms.
    pats = [
        r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+2026\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](2026)\b",
        r"\b(2026)[-/](\d{1,2})[-/](\d{1,2})\b",
    ]
    months = {m.lower(): i for i,m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
    for p in pats:
        m = re.search(p, text, re.I)
        if not m:
            continue
        try:
            if p.startswith(r"\b(\d{1,2})\s+"):
                d = int(m.group(1)); mo = months[m.group(2)[:3].lower()]
                return datetime(2026, mo, d).date()
            if m.group(1) == "2026":
                return datetime(2026, int(m.group(2)), int(m.group(3))).date()
            return datetime(2026, int(m.group(3)), int(m.group(2))).date()
        except Exception:
            pass
    return None

def fetch_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if r.status_code >= 400:
            return "", r.url
        soup = BeautifulSoup(r.text, "html.parser")
        return clean(soup.get_text(" ", strip=True))[:120000], r.url
    except Exception:
        return "", url

def make_id(title, company, location, url):
    raw = "|".join([title, company, location, url])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def parse_result(title, url, snippet):
    page, final_url = fetch_page(url)
    text = clean(" ".join([title, snippet, page]))
    low = text.lower()

    if not any(t in low for t in ROLE_TERMS):
        return None
    if not any(t in low for t in AREA_TERMS):
        return None
    if not any(t in low for t in ENTRY_TERMS):
        return None
    if not any(t in low for t in INDIA_TERMS):
        return None

    posted = extract_date(text)
    if not posted or posted < CUTOFF or posted > TODAY:
        return None

    # Try basic title/company/location extraction.
    clean_title = title
    company = ""
    location = "India"
    # Common result-title separator patterns
    parts = re.split(r"\s[-|]\s", title, maxsplit=2)
    if len(parts) >= 2:
        clean_title = parts[0].strip()
        company = parts[1].strip()
    if not company:
        m = re.search(r"(?:at|@)\s+([A-Z][A-Za-z0-9& ._-]{2,60})", title)
        if m:
            company = m.group(1).strip()

    loc_hits = [x.title() for x in INDIA_TERMS if x in low]
    if loc_hits:
        location = loc_hits[0]

    score = 0
    score += 35 if "oracle ebs" in low else 0
    score += 20 if any(x in low for x in ["scm", "supply chain", "procurement", "inventory", "purchasing", "order management"]) else 0
    score += 15 if any(x in low for x in ["fresher", "0-1", "0-2", "1-2", "entry level", "graduate"]) else 0
    score += 15 if any(x in low for x in ["functional", "support", "consultant", "associate consultant"]) else 0
    score += 15 if "india" in low else 0
    score = min(score, 100)

    return {
        "id": make_id(clean_title, company, location, final_url),
        "title": clean_title,
        "company": company or "Company not extracted",
        "location": location,
        "experience": "0-2 years / fresher (verified wording)",
        "posted_date": posted.isoformat(),
        "source": urlparse(final_url).netloc.replace("www.", ""),
        "url": final_url,
        "match_score": score,
        "verified": True,
        "last_checked": TODAY.isoformat(),
    }

def main():
    rows = load()
    by_id = {r["id"]: r for r in rows if "id" in r}
    seen_urls = set()

    for q in QUERIES:
        try:
            results = search_ddg(q)
        except Exception as e:
            print("Search failed:", q, e)
            continue
        for title, url, snippet in results[:15]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                job = parse_result(title, url, snippet)
                if job:
                    by_id[job["id"]] = job
            except Exception as e:
                print("Parse failed:", url, e)

    # Keep only recent verified records in the public dashboard.
    rows = [r for r in by_id.values()
            if r.get("verified") and r.get("posted_date") and
            CUTOFF.isoformat() <= r["posted_date"] <= TODAY.isoformat()]
    save(rows)
    print(f"Saved {len(rows)} verified jobs.")

if __name__ == "__main__":
    main()
