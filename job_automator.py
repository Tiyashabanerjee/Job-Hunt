"""
🚀 Free Job Application Automator
Stack: Python + GitHub Actions + Groq (free AI) + Free Job APIs + Google Sheets + Gmail
Zero cost. Zero servers. Runs every morning at 5AM automatically.
"""

import os
import json
import time
import smtplib
import requests
import gspread
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google.oauth2.service_account import Credentials


# ─────────────────────────────────────────────
# CONFIG — set these as GitHub Secrets
# ─────────────────────────────────────────────
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
GOOGLE_SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS_JSON   = os.environ["GOOGLE_CREDS_JSON"]   # full JSON string of service account
YOUR_EMAIL          = os.environ["YOUR_EMAIL"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
RESUME_TEXT         = os.environ["RESUME_TEXT"]
REMOTE_ONLY         = os.environ.get("REMOTE_ONLY", "false").lower() == "true"

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}

SHEET_HEADERS = [
    "job_id", "title", "company", "location", "remote", "apply_link",
    "posted_at", "salary", "tags", "source", "match_score",
    "match_reasons", "gaps", "keywords_to_add", "cover_letter",
    "resume_improvements", "application_strategy", "status", "date_added"
]


# ─────────────────────────────────────────────
# STEP 1 — Analyze resume with Groq (free)
# ─────────────────────────────────────────────
def analyze_resume(resume_text: str) -> dict:
    print("🔍 Analyzing resume...")
    payload = {
        "model": "llama-3.1-8b-instant",
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional resume analyzer. Return ONLY valid JSON with these fields: "
                    "name (string), email (string), location (string), years_experience (number), "
                    "current_title (string), skills (array of strings, top 15), "
                    "industries (array of strings), seniority_level (junior|mid|senior|lead), "
                    "target_roles (array of 5 ideal job titles based on experience), "
                    "key_achievements (array of top 3 achievements from resume)."
                )
            },
            {"role": "user", "content": f"Analyze this resume:\n\n{resume_text}"}
        ],
        "max_tokens": 1200,
        "temperature": 0.1
    }
    r = requests.post(GROQ_URL, headers=GROQ_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    result = json.loads(r.json()["choices"][0]["message"]["content"])
    result["resume_text"] = resume_text
    print(f"   ✅ Profile: {result.get('name')} | {result.get('current_title')} | {result.get('seniority_level')}")
    print(f"   🎯 Target roles: {', '.join(result.get('target_roles', []))}")
    return result


# ─────────────────────────────────────────────
# STEP 2 — Fetch jobs from free APIs
# ─────────────────────────────────────────────
def fetch_remoteok_jobs() -> list:
    print("🌐 Fetching RemoteOK jobs...")
    try:
        r = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "job-automator/1.0"},
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        jobs = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for job in data:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            posted = datetime.fromisoformat(job["date"].replace("Z", "+00:00")) if job.get("date") else datetime.now(timezone.utc)
            if posted < cutoff:
                continue
            jobs.append({
                "job_id":     f"rok_{job['id']}",
                "title":      job.get("position", ""),
                "company":    job.get("company", "N/A"),
                "location":   "Remote",
                "remote":     True,
                "description": (job.get("description") or "").replace("<", " <")[:3000],
                "apply_link": job.get("url") or job.get("apply_url", ""),
                "posted_at":  job.get("date", ""),
                "salary":     job.get("salary", ""),
                "tags":       ", ".join(job.get("tags") or []),
                "source":     "RemoteOK"
            })
        print(f"   ✅ {len(jobs)} jobs from RemoteOK (last 24h)")
        return jobs
    except Exception as e:
        print(f"   ⚠️  RemoteOK failed: {e}")
        return []


def fetch_arbeitnow_jobs(query: str) -> list:
    print(f"🌐 Fetching Arbeitnow jobs for '{query}'...")
    try:
        r = requests.get(
            "https://www.arbeitnow.com/api/job-board-api",
            params={"search": query},
            timeout=20
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        jobs = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for job in data:
            posted = datetime.fromtimestamp(job.get("created_at", 0), tz=timezone.utc)
            if posted < cutoff:
                continue
            jobs.append({
                "job_id":     f"arb_{job['slug']}",
                "title":      job.get("title", ""),
                "company":    job.get("company_name", "N/A"),
                "location":   job.get("location") or ("Remote" if job.get("remote") else "N/A"),
                "remote":     job.get("remote", False),
                "description": (job.get("description") or "").replace("<", " <")[:3000],
                "apply_link": f"https://www.arbeitnow.com/jobs/{job['slug']}",
                "posted_at":  datetime.fromtimestamp(job["created_at"], tz=timezone.utc).isoformat(),
                "salary":     "",
                "tags":       ", ".join(job.get("tags") or []),
                "source":     "Arbeitnow"
            })
        print(f"   ✅ {len(jobs)} jobs from Arbeitnow (last 24h)")
        return jobs
    except Exception as e:
        print(f"   ⚠️  Arbeitnow failed: {e}")
        return []


def fetch_themuse_jobs() -> list:
    print("🌐 Fetching The Muse jobs...")
    try:
        r = requests.get(
            "https://www.themuse.com/api/public/jobs",
            params={"page": 0, "descending": "true"},
            timeout=20
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        jobs = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for job in results:
            pub = job.get("publication_date", "")
            if pub:
                posted = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if posted < cutoff:
                    continue
            desc = (job.get("contents") or "")
            for tag in ["<p>","</p>","<br>","<ul>","<li>","</li>","</ul>","<strong>","</strong>","<em>","</em>"]:
                desc = desc.replace(tag, " ")
            jobs.append({
                "job_id":     f"muse_{job['id']}",
                "title":      job.get("name", ""),
                "company":    job.get("company", {}).get("name", "N/A"),
                "location":   ", ".join(l["name"] for l in job.get("locations", [])) or "N/A",
                "remote":     any("remote" in l["name"].lower() for l in job.get("locations", [])),
                "description": desc[:3000],
                "apply_link": job.get("refs", {}).get("landing_page", ""),
                "posted_at":  pub,
                "salary":     "",
                "tags":       ", ".join(c["name"] for c in job.get("categories", [])),
                "source":     "The Muse"
            })
        print(f"   ✅ {len(jobs)} jobs from The Muse (last 24h)")
        return jobs
    except Exception as e:
        print(f"   ⚠️  The Muse failed: {e}")
        return []


def filter_relevant_jobs(jobs: list, profile: dict, already_seen: set) -> list:
    target_roles = [r.lower() for r in profile.get("target_roles", [])]
    skills       = [s.lower() for s in profile.get("skills", [])[:8]]
    seen_ids     = set()
    filtered     = []

    for job in jobs:
        jid = job["job_id"]
        if jid in already_seen or jid in seen_ids:
            continue
        if REMOTE_ONLY and not job.get("remote"):
            continue

        # US filter — only skip if location is clearly non-US
        location = job.get("location", "").lower()
        non_us = ["india","uk","germany","france","canada","australia",
            "netherlands","singapore","brazil","spain","italy","poland",
            "sweden","norway","denmark","finland","switzerland","austria",
            "belgium","portugal","mexico","argentina","colombia","philippines",
            "pakistan","bangladesh","nigeria","kenya","south africa","egypt",
            "dubai","uae","london","berlin","paris","toronto","sydney","mumbai",
            "bangalore","delhi","amsterdam","remote - uk","remote - europe",
            "remote - canada","remote - australia"]
        if location and any(kw in location for kw in non_us):
            continue

        # Relevance filter — only needs 1 match now
        text = (job["title"] + " " + job["description"]).lower()
        role_match  = any(role.split()[0] in text for role in target_roles)
        skill_match = sum(1 for sk in skills if sk in text) >= 1

        if role_match or skill_match:
            seen_ids.add(jid)
            filtered.append(job)

    print(f"   🎯 {len(filtered)} relevant new jobs after filtering")
    return filtered[:20]
    """Filter by relevance to profile and remove duplicates."""
    target_roles = [r.lower() for r in profile.get("target_roles", [])]
    skills       = [s.lower() for s in profile.get("skills", [])[:8]]
    seen_ids     = set()
    filtered     = []

    for job in jobs:
        jid = job["job_id"]
        if jid in already_seen or jid in seen_ids:
            continue
        if REMOTE_ONLY and not job.get("remote"):
            continue

        # US only filter
        location = job.get("location", "").lower()
        us_states = ["alabama","alaska","arizona","arkansas","california","colorado",
            "connecticut","delaware","florida","georgia","hawaii","idaho","illinois",
            "indiana","iowa","kansas","kentucky","louisiana","maine","maryland",
            "massachusetts","michigan","minnesota","mississippi","missouri","montana",
            "nebraska","nevada","new hampshire","new jersey","new mexico","new york",
            "north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania",
            "rhode island","south carolina","south dakota","tennessee","texas","utah",
            "vermont","virginia","washington","west virginia","wisconsin","wyoming",
            "nyc","sf","la","chicago","boston","seattle","austin","denver","atlanta",
            "miami","dallas","houston","phoenix","portland","remote","anywhere"]
        if not any(kw in location for kw in us_states):
            continue

        text = (job["title"] + " " + job["description"]).lower()
        role_match  = any(role.split()[0] in text for role in target_roles)
        skill_match = sum(1 for sk in skills if sk in text) >= 2

        if role_match or skill_match:
            seen_ids.add(jid)
            filtered.append(job)

    print(f"   🎯 {len(filtered)} relevant new jobs after filtering")
    return filtered[:20]  # cap at 20 to stay within free API limits


# ─────────────────────────────────────────────
# STEP 3 — AI analysis per job (Groq, free)
# ─────────────────────────────────────────────
def analyze_job(job: dict, profile: dict) -> dict:
    prompt = f"""CANDIDATE RESUME:
{profile['resume_text'][:2000]}

CANDIDATE SUMMARY:
Name: {profile.get('name')}
Title: {profile.get('current_title')} | {profile.get('years_experience')} years | {profile.get('seniority_level')}
Top Skills: {', '.join(profile.get('skills', [])[:10])}
Key Achievements: {' | '.join(profile.get('key_achievements', []))}

JOB POSTING:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Tags: {job['tags']}
Description: {job['description'][:2000]}

Return ONLY valid JSON with these exact fields:
{{
  "match_score": <integer 0-100>,
  "match_reasons": ["reason1", "reason2", "reason3"],
  "gaps": ["gap1", "gap2"],
  "keywords_to_add": ["kw1", "kw2", "kw3", "kw4"],
  "cover_letter": "<3 compelling paragraphs. Para 1: Company-specific hook, no generic openers. Para 2: 2 achievements with numbers. Para 3: Forward-looking close.>",
  "resume_improvements": [
    {{"section": "<name>", "current_issue": "<what's weak>", "improved_version": "<exact new text>", "impact": "<why it helps>"}}
  ],
  "application_strategy": "<1 paragraph of specific advice for this exact role/company>"
}}"""

    payload = {
        "model": "llama-3.3-70b-versatile",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are an elite career coach. Return ONLY valid JSON. No markdown, no preamble."},
            {"role": "user",   "content": prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.65
    }

    r = requests.post(GROQ_URL, headers=GROQ_HEADERS, json=payload, timeout=45)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    result  = json.loads(content)
    result["_processed_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ─────────────────────────────────────────────
# STEP 4 — Google Sheets (read + write)
# ─────────────────────────────────────────────
def get_sheets_client():
    creds_data = json.loads(GOOGLE_CREDS_JSON)
    scopes     = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds      = Credentials.from_service_account_info(creds_data, scopes=scopes)
    return gspread.authorize(creds)


def get_already_seen_ids(sheet) -> set:
    try:
        rows = sheet.get_all_records()
        return {r["job_id"] for r in rows if r.get("job_id")}
    except Exception:
        return set()


def ensure_sheet_headers(sheet):
    try:
        first_row = sheet.row_values(1)
        if first_row != SHEET_HEADERS:
            sheet.insert_row(SHEET_HEADERS, 1)
            print("   ✅ Sheet headers created")
    except Exception as e:
        print(f"   ⚠️  Could not set headers: {e}")


def write_job_to_sheet(sheet, job: dict, ai: dict):
    row = [
        job.get("job_id", ""),
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        str(job.get("remote", False)),
        job.get("apply_link", ""),
        job.get("posted_at", ""),
        job.get("salary", ""),
        job.get("tags", ""),
        job.get("source", ""),
        ai.get("match_score", 0),
        " | ".join(ai.get("match_reasons", [])),
        " | ".join(ai.get("gaps", [])),
        ", ".join(ai.get("keywords_to_add", [])),
        ai.get("cover_letter", ""),
        json.dumps(ai.get("resume_improvements", [])),
        ai.get("application_strategy", ""),
        "To Apply",
        ai.get("_processed_at", "")
    ]
    sheet.append_row(row, value_input_option="RAW")


# ─────────────────────────────────────────────
# STEP 5 — Send Gmail digest
# ─────────────────────────────────────────────
def send_email_digest(jobs_with_ai: list, sheet_id: str):
    if not jobs_with_ai:
        print("📭 No new jobs to email.")
        return

    sorted_jobs = sorted(jobs_with_ai, key=lambda x: x[1].get("match_score", 0), reverse=True)
    today_str   = datetime.now().strftime("%A, %b %-d")
    sheet_url   = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    def score_color(score):
        if score >= 80: return "#16a34a"
        if score >= 60: return "#d97706"
        return "#dc2626"

    job_cards = ""
    for job, ai in sorted_jobs:
        sc    = ai.get("match_score", 0)
        color = score_color(sc)
        sal   = f"<p style='margin:4px 0;color:#555'>💰 {job['salary']}</p>" if job.get("salary") else ""
        job_cards += f"""
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:22px;margin:16px 0;background:white">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
            <div>
              <h2 style="margin:0 0 4px;color:#111;font-size:18px">{job['title']}</h2>
              <p style="margin:0 0 4px;color:#555;font-size:14px">{job['company']} &middot; {job['location']}{'&nbsp;🌐' if job.get('remote') else ''}</p>
              {sal}
              <span style="font-size:12px;background:#f3f4f6;padding:3px 8px;border-radius:99px;color:#555">{job['source']}</span>
            </div>
            <div style="text-align:center;background:{color}15;border-radius:12px;padding:10px 16px;min-width:60px">
              <div style="font-size:26px;font-weight:800;color:{color};line-height:1">{sc}</div>
              <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:.5px">Match</div>
            </div>
          </div>
          <p style="margin:14px 0 6px;font-size:13px"><strong>✅ Why you match:</strong> {' &bull; '.join(ai.get('match_reasons', []))}</p>
          <p style="margin:6px 0;font-size:13px"><strong>⚠️ Gaps to address:</strong> {' &bull; '.join(ai.get('gaps', []))}</p>
          <p style="margin:6px 0;font-size:13px"><strong>🔑 Keywords to add:</strong> <em>{', '.join(ai.get('keywords_to_add', []))}</em></p>
          <p style="margin:6px 0;font-size:13px"><strong>📋 Strategy:</strong> {ai.get('application_strategy', '')}</p>
          <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
            <a href="{job['apply_link']}" style="background:#4f46e5;color:white;padding:9px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px">Apply Now →</a>
            <a href="{sheet_url}" style="background:#f9fafb;color:#374151;border:1px solid #d1d5db;padding:9px 18px;border-radius:8px;text-decoration:none;font-size:13px">View Cover Letter in Sheet</a>
          </div>
        </div>"""

    html = f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f3f4f6;margin:0;padding:24px">
    <div style="max-width:680px;margin:auto">
      <div style="background:white;border-radius:16px;padding:32px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
        <h1 style="margin:0 0 8px;color:#111">🚀 Your Daily Job Report</h1>
        <p style="color:#6b7280;margin:0">Good morning! <strong>{len(sorted_jobs)} new roles</strong> found for {today_str}, ranked by match score.</p>
      </div>
      {job_cards}
      <p style="text-align:center;color:#9ca3af;font-size:12px;padding:16px 0">
        Generated by your free Python Job Automator &middot;
        <a href="{sheet_url}" style="color:#4f46e5">Open Full Google Sheet</a>
      </p>
    </div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚀 {len(sorted_jobs)} New Jobs Found — {today_str}"
    msg["From"]    = YOUR_EMAIL
    msg["To"]      = YOUR_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(YOUR_EMAIL, GMAIL_APP_PASSWORD)
        smtp.sendmail(YOUR_EMAIL, YOUR_EMAIL, msg.as_string())

    print(f"📧 Email digest sent → {YOUR_EMAIL}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("\n" + "="*55)
    print("  🚀 Free Job Automator — Starting Run")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("="*55 + "\n")

    # 1. Analyze resume
    profile = analyze_resume(RESUME_TEXT)

    # 2. Connect to Google Sheets
    print("\n📊 Connecting to Google Sheets...")
    gc         = get_sheets_client()
    spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
    try:
        sheet = spreadsheet.worksheet("Jobs")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet("Jobs", rows=1000, cols=20)
        print("   ✅ Created 'Jobs' worksheet")
    ensure_sheet_headers(sheet)
    already_seen = get_already_seen_ids(sheet)
    print(f"   ✅ {len(already_seen)} previously processed jobs found")

    # 3. Fetch & filter jobs
    print("\n🔎 Fetching jobs from free APIs...")
    primary_role = profile.get("target_roles", ["software engineer"])[0]
    all_jobs = (
        fetch_remoteok_jobs() +
        fetch_arbeitnow_jobs(primary_role) +
        fetch_themuse_jobs()
    )
    relevant_jobs = filter_relevant_jobs(all_jobs, profile, already_seen)

    if not relevant_jobs:
        print("\n😴 No new relevant jobs found today. Exiting.")
        return

    # 4. AI analysis + write to sheet
    print(f"\n🤖 Running AI analysis on {len(relevant_jobs)} jobs (Groq)...")
    jobs_with_ai = []
    for i, job in enumerate(relevant_jobs, 1):
        print(f"   [{i}/{len(relevant_jobs)}] {job['title']} @ {job['company']}")
        try:
            ai = analyze_job(job, profile)
            write_job_to_sheet(sheet, job, ai)
            jobs_with_ai.append((job, ai))
            print(f"          ✅ Match score: {ai.get('match_score')} | Written to sheet")
            time.sleep(1.5)  # stay within Groq free rate limits
        except Exception as e:
            print(f"          ⚠️  Skipped: {e}")
            time.sleep(3)

    # 5. Send email
    print("\n📧 Sending email digest...")
    send_email_digest(jobs_with_ai, GOOGLE_SHEET_ID)

    print(f"\n✅ Done! {len(jobs_with_ai)} jobs processed and emailed.\n")


if __name__ == "__main__":
    main()
