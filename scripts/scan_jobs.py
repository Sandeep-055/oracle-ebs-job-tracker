#!/usr/bin/env python3
import os,re,json,hashlib,requests
from datetime import date,timedelta
from urllib.parse import quote,urlparse
from bs4 import BeautifulSoup

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA=os.path.join(ROOT,'data','jobs.json')
TODAY=date.today(); CUTOFF=TODAY-timedelta(days=10)
H={'User-Agent':'Mozilla/5.0 (compatible; Oracle-EBS-Job-Tracker/1.0)'}
QUERIES=['"Oracle EBS" SCM fresher India jobs','"Oracle EBS" "0-2 years" India jobs','"Oracle EBS" "1-2 years" India jobs','"Oracle EBS" "Associate Consultant" India','"Oracle EBS" "Junior Consultant" India','"Oracle EBS" "Functional Consultant" India','"Oracle EBS" support "0-2" India','"Oracle EBS" Finance fresher India']
INDIA=['india','hyderabad','secunderabad','bengaluru','bangalore','chennai','pune','mumbai','delhi','noida','gurugram','gurgaon','kolkata','jaipur','ahmedabad','kochi','coimbatore','indore','bhubaneswar','visakhapatnam','vijayawada']
ENTRY=['fresher','freshers','entry level','entry-level','0-1','0–1','0 to 1','0-2','0–2','0 to 2','1-2','1–2','1 to 2','graduate','junior']
ROLE=['oracle ebs','oracle apps','oracle applications','e-business suite','r12']
AREA=['scm','supply chain','inventory','purchasing','procurement','order management','finance','functional','support','consultant','business analyst','associate consultant','junior consultant']
MONTHS={'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

def clean(s): return re.sub(r'\\s+',' ',s or '').strip()
def fetch(url):
    try:
        r=requests.get(url,headers=H,timeout=15,allow_redirects=True)
        return (r.text if r.status_code<400 else ''),r.url
    except requests.RequestException:return '',url

def search(q):
    html,_=fetch('https://html.duckduckgo.com/html/?q='+quote(q))
    if not html:return []
    soup=BeautifulSoup(html,'html.parser'); out=[]
    for a in soup.select('a.result__a'):
        href=a.get('href'); title=clean(a.get_text(' ',strip=True)); p=a.find_parent('div',class_='result')
        snippet=clean(p.get_text(' ',strip=True) if p else '')
        if href and title:out.append((title,href,snippet))
    return out

def pdate(text):
    pats=[r'\\b(\\d{1,2})\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s*,?\\s*(20\\d{2})\\b',r'\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+(\\d{1,2}),\\s*(20\\d{2})\\b',r'\\b(20\\d{2})[-/](\\d{1,2})[-/](\\d{1,2})\\b',r'\\b(\\d{1,2})[-/](\\d{1,2})[-/](20\\d{2})\\b']
    for i,p in enumerate(pats):
        m=re.search(p,text,re.I)
        if not m:continue
        try:
            if i==0:return date(int(m.group(3)),MONTHS[m.group(2).lower()[:3]],int(m.group(1)))
            if i==1:return date(int(m.group(3)),MONTHS[m.group(1).lower()[:3]],int(m.group(2)))
            if i==2:return date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
            return date(int(m.group(3)),int(m.group(2)),int(m.group(1)))
        except ValueError:return None
    return None

def make_job(title,url,snippet):
    html,final=fetch(url); page=clean(BeautifulSoup(html,'html.parser').get_text(' ',strip=True)) if html else ''
    text=clean(' '.join([title,snippet,page])); low=text.lower()
    if not any(x in low for x in ROLE) or not any(x in low for x in AREA) or not any(x in low for x in ENTRY) or not any(x in low for x in INDIA):return None
    d=pdate(text)
    if not d or d<CUTOFF or d>TODAY:return None
    if re.search(r'\\b(?:3|4|5|6|7|8|9|10)\\s*\\+?\\s*(?:years?|yrs?)\\s+(?:of\\s+)?(?:experience|exp)\\b',low) and not re.search(r'\\b(?:0|1|2)\\s*(?:-|to|–)\\s*[12]\\s*(?:years?|yrs?)',low):return None
    parts=re.split(r'\\s[-|]\\s',title,maxsplit=2); company=''; clean_title=title
    if len(parts)>=2:clean_title,company=clean(parts[0]),clean(parts[1])
    loc=next((x.title() for x in INDIA if x in low and x!='india'),'India')
    score=(35 if 'oracle ebs' in low else 25)+(20 if any(x in low for x in ['scm','supply chain','inventory','purchasing','procurement','order management']) else 10)+(15 if any(x in low for x in ENTRY) else 0)+(15 if any(x in low for x in ['functional','support','consultant','business analyst']) else 0)+(15 if 'india' in low else 5)
    uid=hashlib.sha256('|'.join([clean_title,company,loc,final]).encode()).hexdigest()[:20]
    return {'id':uid,'title':clean_title,'company':company or 'Company not extracted','location':loc,'experience':'0–2 years / fresher wording verified','posted_date':d.isoformat(),'source':urlparse(final).netloc.replace('www.',''),'url':final,'match_score':min(score,100),'verified':True,'last_checked':TODAY.isoformat()}

def load():
    try:
        with open(DATA,encoding='utf-8') as f:return json.load(f)
    except (OSError,ValueError):return []
def save(rows):
    os.makedirs(os.path.dirname(DATA),exist_ok=True)
    rows.sort(key=lambda x:(x.get('posted_date',''),x.get('match_score',0)),reverse=True)
    with open(DATA,'w',encoding='utf-8') as f:json.dump(rows,f,ensure_ascii=False,indent=2)

def main():
    old=load(); by={x.get('id'):x for x in old if x.get('id')}; seen=set()
    for q in QUERIES:
        print('Searching:',q)
        try: results=search(q)
        except Exception as e: print('search failed',type(e).__name__); continue
        for title,url,snip in results[:15]:
            if url in seen:continue
            seen.add(url)
            try:
                j=make_job(title,url,snip)
                if j:by[j['id']]=j;print(' +',j['title'])
            except Exception as e:print(' skipped',type(e).__name__)
    rows=[x for x in by.values() if x.get('verified') and x.get('posted_date') and CUTOFF.isoformat()<=x['posted_date']<=TODAY.isoformat()]
    save(rows);print('Saved',len(rows),'verified jobs')
if __name__=='__main__':main()
 
