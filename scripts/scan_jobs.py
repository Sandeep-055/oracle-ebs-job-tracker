#!/usr/bin/env python3
"""Resume-targeted Oracle EBS SCM job scanner for India.
Only retains jobs with a verifiable posting date in the last 7 days and India location.
Experience is extracted where available; uncertain experience is flagged for manual review.
"""
import os, re, json, hashlib, requests
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'jobs.json')
TODAY = date.today()
CUTOFF = TODAY - timedelta(days=7)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ResumeTargetedOracleEBSJobTracker/2.0)'}

# Search terms derived from Sandeep's resume; avoid finance-only and developer-only roles.
QUERIES = [
    'Oracle EBS SCM Functional Consultant India 0-2 years',
    'Oracle EBS R12 Purchasing Inventory Order Management India fresher',
    'Oracle EBS SCM Associate Consultant India',
    'Oracle EBS SCM Production Support India 1-2 years',
    'Oracle EBS Oracle Sourcing iProcurement functional India',
    'Oracle EBS P2P S2P O2C functional support India',
    'Oracle EBS GST TDS EBTax functional consultant India junior',
    'Oracle EBS SCM functional consultant Hyderabad junior',
]
INDIA_TERMS = ['india','hyderabad','secunderabad','telangana','bengaluru','bangalore','chennai','tamil nadu','pune','mumbai','maharashtra','delhi','noida','gurugram','gurgaon','kolkata','jaipur','ahmedabad','kochi','coimbatore','indore','bhubaneswar','visakhapatnam','vijayawada','remote india']
SCM_TERMS = ['scm','supply chain','purchasing','inventory','order management','oracle sourcing','iprocurement','procurement','p2p','s2p','o2c','shipping execution','receiving','rma','oracle ebs scm']
ROLE_TERMS = ['oracle ebs','oracle apps','oracle applications','e-business suite','e-business','r12']
ENTRY_TERMS = ['fresher','freshers','entry level','entry-level','graduate trainee','junior','associate consultant','0-1 years','0 to 1 year','0-2 years','0 to 2 years','1-2 years','1 to 2 years','1+ year','1 year experience','2 years experience']
BAD_TITLE = re.compile(r'\b(senior|sr\.?|lead|manager|architect|principal|director|developer|technical developer|pl/sql developer)\b', re.I)
MONTHS = {m.lower(): i for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}


def clean(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def fetch(url, params=None, headers=None):
    try:
        r = requests.get(url, params=params, headers=headers or HEADERS, timeout=18)
        if r.status_code >= 400: return None
        return r
    except requests.RequestException:
        return None


def parse_date(value):
    if not value: return None
    if isinstance(value, (int, float)):
        try: return datetime.fromtimestamp(value, timezone.utc).date()
        except (ValueError, OSError): return None
    s = clean(value)
    # ISO timestamps/dates and common API formats.
    try: return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    except ValueError: pass
    for fmt in ('%Y-%m-%d', '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%d/%m/%Y', '%m/%d/%Y'):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: continue
    m = re.search(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,?\s*(20\d{2})\b', s, re.I)
    if m:
        try: return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError: return None
    # Relative dates are only accepted when explicitly stated by the listing/search result.
    m = re.search(r'\b(today|just posted|yesterday|(\d+)\s+days? ago)\b', s, re.I)
    if m:
        days = 0 if m.group(1).lower() in ('today','just posted') else 1 if m.group(1).lower() == 'yesterday' else int(m.group(2))
        return TODAY - timedelta(days=days)
    return None


def india_location(loc, text=''):
    s = (clean(loc) + ' ' + clean(text)).lower()
    if re.search(r'\b(united states|usa|united kingdom|canada|australia|singapore|uae|dubai|saudi arabia|germany|ireland|philippines|malaysia)\b', s):
        return False
    return any(term in s for term in INDIA_TERMS)


def experience_fit(text, title=''):
    s = (clean(title) + ' ' + clean(text)).lower()
    if re.search(r'\b(?:3|4|5|6|7|8|9|10)\s*\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s*)?(?:hands-on\s*)?(?:experience|exp)?', s):
        # A clear 0-2 range elsewhere is still acceptable only if it is the job requirement.
        if not re.search(r'\b(?:0\s*(?:-|to|–)\s*2|1\s*(?:-|to|–)\s*2)\s*(?:years?|yrs?)\b', s):
            return False, 'Over 2 years mentioned — excluded'
    m = re.search(r'\b(\d+)\s*(?:-|to|–|—)\s*(\d+)\s*(years?|yrs?|months?)\b', s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 'month' in m.group(3): return (hi <= 24, m.group(0))
        return (hi <= 2, m.group(0))
    if re.search(r'\b(fresher|freshers|entry[- ]level|graduate trainee|no experience required|0\s+years? experience)\b', s):
        return True, 'Fresher / entry-level wording'
    if re.search(r'\b(1\+?|one)\s+years?\s+(?:of\s+)?(?:relevant\s+)?experience\b', s):
        return True, '1+ year experience wording'
    if re.search(r'\b(?:junior|associate consultant|associate functional consultant)\b', title, re.I) and not re.search(r'\b(?:3|4|5|6|7|8|9|10)\s*\+?\s*(?:years?|yrs?)\b', s):
        return None, 'Experience not stated clearly — review listing'
    return None, 'Experience requirement not confirmed — review listing'


def role_match(title, text):
    s = (clean(title) + ' ' + clean(text)).lower()
    if not any(x in s for x in ROLE_TERMS): return False
    if not any(x in s for x in SCM_TERMS): return False
    if BAD_TITLE.search(title): return False
    # Finance-only positions are not the target; finance keywords remain useful if SCM is also present.
    return True


def make_record(title, company, location, description, url, posted, source, experience_text=''):
    title, company, location = clean(title), clean(company), clean(location)
    description = clean(description)
    d = parse_date(posted)
    if not d or d < CUTOFF or d > TODAY: return None
    if not india_location(location, title + ' ' + description[:800]): return None
    if not role_match(title, description): return None
    ok, exp_label = experience_fit(experience_text + ' ' + description, title)
    if ok is False: return None
    text = (title + ' ' + description).lower()
    skills = [x for x in ['p2p','s2p','o2c','purchasing','inventory','order management','oracle sourcing','iprocurement','gst','tds','ebtax','ame','3-way match','autoinvoice','receiving','rma','sql','br100','sit','uat'] if x in text]
    score = 30 + min(30, len(set(skills)) * 3) + (15 if 'hyderabad' in location.lower() or 'telangana' in location.lower() else 0) + (15 if ok is True else 5) + (10 if any(x in title.lower() for x in ['functional','associate','junior','support']) else 0)
    uid = hashlib.sha256('|'.join([title.lower(), company.lower(), url]).encode()).hexdigest()[:20]
    return {
        'id': uid, 'title': title, 'company': company or 'Company not extracted', 'location': location or 'India',
        'experience': exp_label, 'experience_status': 'eligible wording found' if ok is True else 'review required',
        'posted_date': d.isoformat(), 'posted_age_days': (TODAY-d).days, 'source': source or urlparse(url).netloc,
        'url': url, 'match_score': min(100, score), 'verified': True, 'last_checked': TODAY.isoformat(),
        'resume_skills_matched': skills, 'description': description[:5000]
    }


def adzuna_results():
    app_id, app_key = os.getenv('ADZUNA_APP_ID'), os.getenv('ADZUNA_APP_KEY')
    if not app_id or not app_key: return []
    out=[]
    for q in ['Oracle EBS SCM functional','Oracle EBS purchasing inventory order management','Oracle EBS SCM associate consultant']:
        r=fetch('https://api.adzuna.com/v1/api/jobs/in/search/1', {
            'app_id':app_id, 'app_key':app_key, 'results_per_page':50, 'what':q, 'where':'India', 'max_days_old':7, 'content-type':'application/json'
        })
        if not r: continue
        try: jobs=r.json().get('results', [])
        except ValueError: continue
        for j in jobs:
            url=j.get('redirect_url') or j.get('url') or ''
            if not url: continue
            rec=make_record(j.get('title'), (j.get('company') or {}).get('display_name',''), (j.get('location') or {}).get('display_name','India'), j.get('description',''), url, j.get('created'), 'Adzuna', j.get('contract_type',''))
            if rec: out.append(rec)
    return out


def jsearch_results():
    key=os.getenv('JSEARCH_API_KEY')
    if not key: return []
    out=[]
    headers={**HEADERS,'X-RapidAPI-Key':key,'X-RapidAPI-Host':'jsearch.p.rapidapi.com'}
    for q in ['Oracle EBS SCM functional consultant in India','Oracle EBS SCM associate consultant India']:
        r=fetch('https://jsearch.p.rapidapi.com/search', {'query':q,'page':'1','num_pages':'1','date_posted':'week'}, headers)
        if not r: continue
        try: jobs=r.json().get('data', [])
        except ValueError: continue
        for j in jobs:
            posted=j.get('job_posted_at_datetime_utc') or j.get('job_posted_at_timestamp') or j.get('job_posted_at')
            rec=make_record(j.get('job_title'), j.get('employer_name',''), j.get('job_location') or ('India' if j.get('job_country')=='IN' else ''), j.get('job_description',''), j.get('job_apply_link') or j.get('job_google_link') or '', posted, j.get('job_publisher','JSearch'), j.get('job_required_experience',{}).get('required_experience_text','') if isinstance(j.get('job_required_experience'),dict) else '')
            if rec: out.append(rec)
    return out


def ddg_results():
    out=[]
    for q in QUERIES:
        print('Searching web:',q)
        r=fetch('https://html.duckduckgo.com/html/', {'q':q}, HEADERS)
        if not r: continue
        soup=BeautifulSoup(r.text,'html.parser')
        for a in soup.select('a.result__a')[:12]:
            url=a.get('href') or ''; title=clean(a.get_text(' ',strip=True))
            p=a.find_parent('div',class_='result'); snippet=clean(p.get_text(' ',strip=True) if p else '')
            if not url or not title: continue
            # Search snippet date is used only if explicitly present; never infer from crawler dates.
            posted=parse_date(snippet)
            if not posted: continue
            rec=make_record(title, '', 'India' if 'India' in snippet or 'India' in title else snippet, snippet, url, posted, urlparse(url).netloc)
            if rec: out.append(rec)
    return out


def main():
    found=[]
    for source_fn in (adzuna_results, jsearch_results, ddg_results):
        try:
            rows=source_fn(); print(source_fn.__name__, 'returned', len(rows))
            found.extend(rows)
        except Exception as e: print(source_fn.__name__, 'failed:', type(e).__name__, str(e)[:180])
    by={}
    for j in found:
        key=(j.get('title','').lower(),j.get('company','').lower(),j.get('location','').lower())
        if key not in by or j.get('match_score',0)>by[key].get('match_score',0): by[key]=j
    rows=sorted(by.values(),key=lambda x:(x.get('posted_date',''),x.get('match_score',0)),reverse=True)
    os.makedirs(os.path.dirname(DATA),exist_ok=True)
    with open(DATA,'w',encoding='utf-8') as f: json.dump(rows,f,ensure_ascii=False,indent=2)
    print(f'Saved {len(rows)} deduplicated jobs posted {CUTOFF.isoformat()} through {TODAY.isoformat()}.')
    if not os.getenv('ADZUNA_APP_ID') or not os.getenv('ADZUNA_APP_KEY'):
        print('NOTE: Adzuna API is not configured; add ADZUNA_APP_ID and ADZUNA_APP_KEY GitHub Actions secrets.')
    if not os.getenv('JSEARCH_API_KEY'): print('NOTE: JSearch is optional; add JSEARCH_API_KEY to include supported portal listings.')

if __name__=='__main__': main()
