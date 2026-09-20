# Oracle EBS Daily Job Tracker — GitHub Pages + Actions

This version does NOT require a laptop to stay on and does NOT use Google Cloud.

Architecture:
- GitHub Pages = website
- GitHub Actions = daily scanner
- data/jobs.json = job database
- scripts/scan_jobs.py = scanner

One-time setup:
1. Create a public GitHub repository.
2. Upload all files from this project.
3. In Settings -> Pages, select GitHub Actions as the source.
4. In Settings -> Actions -> General, allow GitHub Actions to run.
5. Run the workflow once manually from Actions -> Daily Oracle EBS Job Scan.
6. GitHub Pages will publish the website.

Important:
- The scanner uses publicly accessible search results/pages only.
- It does not bypass login, CAPTCHA, anti-bot controls, or robots restrictions.
- A job is included only when the scanner can verify Oracle EBS relevance, India location,
  0-2/fresher/entry-level wording, and a posting date within 10 days.
- Job portals can block automated access, so this is a strict verified-results tracker,
  not a guaranteed exhaustive count of every job on the internet.
- For production-grade portal APIs, add official API credentials/secrets where available.

Daily schedule:
- The GitHub Actions workflow runs once every day.
- You can also run it manually from the Actions tab.

Local testing is optional; the website itself is static and can be opened without Python.
