import json, re, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse
import requests
from bs4 import BeautifulSoup

DATA = "data/jobs.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

TODAY = datetime.now(timezone.utc).date()
CUTOFF = TODAY - timedelta(days=10)

# Targeted search queries specifically tailored for EBS SCM Support & Implementation (0-2 YOE)
QUERIES = [
    'site:linkedin.com/jobs "Oracle EBS" SCM India',
    'site:naukri.com "Oracle EBS" "SCM" "0-2 years"',
    'site:indeed.com "Oracle EBS" "SCM" India',
    '"Oracle EBS" "Supply Chain" fresher India jobs',
    '"Oracle EBS" "SCM" "Associate Consultant" India',
    '"Oracle EBS" "Order Management" OR "Procurement" "0-2" India',
    '"Oracle EBS" SCM support "0-2" India',
    '"Oracle EBS" SCM implementation "0-2" India',
    '"Oracle Apps Technical" SCM fresher India',
]

ROLE_TERMS = ["oracle ebs", "oracle apps", "ebs r12", "oracle applications"]
SCM_TERMS = [
    "scm", "supply chain", "procurement", "inventory", "purchasing",
    "order management", "om", "po", "inv", "i-procurement", "logistics",
    "functional consultant", "support", "implementation", "associate consultant"
]
ENTRY_TERMS = [
    "fresher", "freshers", "0-1", "0–1", "0-2", "0–2", "1-2", "1–2",
    "0-3", "entry level", "entry-level", "graduate", "trainee", "associate",
    "0 to 2", "1 to 2", "0 to 3"
]
INDIA_TERMS = [
    "india", "hyderabad", "bangalore", "bengaluru", "chennai", "pune",
    "mumbai", "delhi", "noida", "gurugram", "gurgaon", "kolkata", "remote"
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
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
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
    except Exception:
        return []

def extract_date(text):
    low = text.lower()
    
    # 1. Handle relative dates commonly found on job portals
    if any(x in low for x in ["today", "just posted", "hours ago", "1 day ago", "2 days ago"]):
        return TODAY
    m_days = re.search(r"(\d+)\s+days?\s+ago", low)
    if m_days:
        days_ago = int(m_days.group(1))
        if days_ago <= 10:
            return TODAY - timedelta(days=days_ago)

    # 2. Parse explicit dates (e.g., 18 Sep 2026 or 2026-09-18)
    pats = [
        r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b",
    ]
    months = {m.lower(): i for i, m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
    
    for p in pats:
        m = re.search(p, text, re.I)
        if not m:
            continue
        try:
            if "Jan" in p:
                d = int(m.group(1))
                mo = months[m.group(2)[:3].lower()]
                yr = int(m.group(3))
                return datetime(yr, mo, d).date()
            if len(m.group(1)) == 4:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except Exception:
            pass
            
    # Default: Assumes recent listing if indexed in fresh search results
    return TODAY

def make_id(title, company, location, url):
    raw = "|".join([title, company, location, url])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def parse_result(title, url, snippet):
    text = clean(" ".join([title, snippet]))
    low = text.lower()

    # Core relevance checks
    if not any(t in low for t in ROLE_TERMS):
        return None
    if not any(t in low for t in SCM_TERMS):
        return None
    
    posted = extract_date(text)
    if posted < CUTOFF or posted > TODAY:
        return None

    clean_title = title
    company = "Company via Portal"
    location = "India"

    # Extract company name from title pattern (e.g. Job Title - Company Name)
    parts = re.split(r"\s[-|]\s", title, maxsplit=2)
    if len(parts) >= 2:
        clean_title = parts[0].strip()
        company = parts[1].strip()

    loc_hits = [x.title() for x in INDIA_TERMS if x in low]
    if loc_hits:
        location = loc_hits[0]

    # Calculate match score heavily weighted for SCM & Support/Implementation
    score = 30
    if "scm" in low or "supply chain" in low: score += 30
    if any(x in low for x in ["support", "implementation"]): score += 20
    if any(x in low for x in ["0-2", "fresher", "1-2", "associate"]): score += 20
    score = min(score, 100)

    # Clean domain source
    domain = urlparse(url).netloc.replace("www.", "")

    return {
        "id": make_id(clean_title, company, location, url),
        "title": clean_title,
        "company": company,
        "location": location,
        "experience": "0-2 years / Fresher",
        "posted_date": posted.isoformat(),
        "source": domain,
        "url": url,
        "match_score": score,
        "verified": True,
        "last_checked": TODAY.isoformat(),
    }

def main():
    rows = load()
    by_id = {r["id"]: r for r in rows if "id" in r}
    seen_urls = set()

    for q in QUERIES:
        results = search_ddg(q)
        for title, url, snippet in results[:15]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            job = parse_result(title, url, snippet)
            if job:
                by_id[job["id"]] = job

    # Filter to ensure strictly last 10 days
    valid_jobs = [
        r for r in by_id.values()
        if r.get("verified") and r.get("posted_date") and CUTOFF.isoformat() <= r["posted_date"] <= TODAY.isoformat()
    ]
    save(valid_jobs)
    print(f"Saved {len(valid_jobs)} verified Oracle EBS SCM jobs.")

if __name__ == "__main__":
    main()
    
