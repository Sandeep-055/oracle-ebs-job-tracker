# Oracle EBS Daily Job Tracker (GitHub Pages + GitHub Actions)

A free website that lists Oracle EBS SCM / Finance / functional / support jobs in India (0-2 years, posted in the last 10 days) and updates twice a day (06:15 and 18:00 IST) without any computer of yours being on.

```
.github/workflows/daily-scan.yml   runs the scan, saves data/, publishes the site
scripts/scan_jobs.py               the scanner
static/index.html                  the website (dashboard, filters, resume match, tracker)
config/sources.json                optional: employer job boards / job pages to watch
data/jobs.json, data/status.json   written by the scan
tests/test_scan.py                 offline self-tests (run before every scan)
```

## One-time setup
1. Upload these files to the repo, replacing the old ones (`daily-scan.yml`, `scan_jobs.py`, `index.html`; the rest is optional).
2. Get a free job-search key: sign up at **developer.adzuna.com** and copy the *App ID* and *App Key*.
3. Repo **Settings > Secrets and variables > Actions > New repository secret**. Add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`.
   Optional: `JSEARCH_API_KEY` (RapidAPI "JSearch") adds listings that come from LinkedIn, Indeed, Naukri and Glassdoor.
4. Repo **Settings > Pages > Source: GitHub Actions**.
5. **Actions > Daily Oracle EBS Job Scan > Run workflow.** When it turns green, open `https://<your-username>.github.io/<repo-name>/`.

## What counts as a job on the site
- Oracle EBS in the title or description, a functional / support / associate / junior role, SCM or Finance focus, India location.
- Not senior/lead/manager, not technical-only, not Fusion/Cloud-only, and no more than 2 years asked.
- **Posting date comes from the source** (never guessed). No date means the job is flagged and hidden.
- **"Active" needs evidence.** It is shown only if a source listed it in the last 2 days, or its official page still loads without closure text. Employer boards (Greenhouse/Lever) that drop a job mark it closed straight away.
- Jobs are merged across sources into one card; the company's own application link is preferred over a portal or aggregator link.
- Every scan writes which sources answered, were not set up, or are unavailable. The Dashboard shows this.

## Sources
| Source | Needs | Notes |
|---|---|---|
| Adzuna India | free key | main source; official API |
| JSearch (RapidAPI) | key, optional | carries LinkedIn / Indeed / Naukri / Glassdoor listings; free tier is small, so only 2 queries a run |
| Greenhouse, Lever | `config/sources.json` | real employer boards only |
| Job pages with schema.org JobPosting data | `config/sources.json` | robots.txt is honoured |
| LinkedIn, Naukri, Indeed, Glassdoor, foundit directly | not possible | no public API and their terms forbid automated access. Use the search links on the site's *Add jobs* page and add what you find. |

The scanner never bypasses logins, CAPTCHAs, robots.txt or rate limits.

## Your resume and tracker
Everything personal (resume profile, saved jobs, application tracker, settings) stays in your browser. The resume is read on your device and is never uploaded. An optional Gemini key in Settings adds an AI second opinion per job.

## If something is wrong
- **Site is empty / "Could not load data/jobs.json":** run the workflow once, and check Settings > Pages > Source is *GitHub Actions*.
- **"No job source answered":** the two Adzuna secrets are missing or wrong.
- **Workflow red:** open the run; the *Self-test* step protects the scanner from a bad edit.
- **Runs stopped after a long quiet period:** GitHub pauses scheduled runs on inactive repos. Press *Enable* on the Actions tab.
- Few or zero jobs on some days is normal. It means nothing verified matched, not that the scan failed.
- 
