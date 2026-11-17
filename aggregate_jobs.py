#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily Data / AI job alert for Switzerland, sent by e-mail (HTML).

Sources: SwissDevJobs (RSS), jobs.ch + jobup.ch (JobCloud API), Greenhouse,
Google Careers (Zurich/Geneva, includes DeepMind), Workday (WORKDAY_SITES),
LinkedIn alerts read via IMAP (optional, LINKEDIN_IMAP=1).

Environment variables: EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD (required to send)
  MAX_DAYS_OLD=1    window in days (default: 1 = last 24h)
  INCLUDE_GERMAN=1  add German keywords
  LINKEDIN_IMAP=1   read LinkedIn alerts from the Gmail inbox
Usage: python aggregate_jobs.py [--dry-run]   (--dry-run writes rapport.html without sending)
Dependencies: pip install requests
"""
import email as email_lib
import html
import imaplib
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

# =============================== CONFIG ===============================
MAX_DAYS_OLD = int(os.environ.get("MAX_DAYS_OLD", "1"))  # 24h for daily alerts
REQUEST_DELAY = 0.4          # pause between requests (politeness)
JOBCLOUD_MAX_PAGES = 3       # pages per query on jobs.ch / jobup.ch
LINKEDIN_DAYS = 2            # how far back to read LinkedIn alert e-mails

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
INCLUDE_GERMAN = os.environ.get("INCLUDE_GERMAN", "0") == "1"
LINKEDIN_IMAP = os.environ.get("LINKEDIN_IMAP", "0") == "1"
PRIORITY_MIN_SCORE = 3       # minimum score for the "priority" section

# Keywords matched against the TITLE, on whole words (see matches_keywords).
KEYWORDS_EN = [
    "data scientist", "data science", "data analyst", "data engineer",
    "research scientist", "research engineer", "applied scientist",
    "ai engineer", "ai consultant", "industrial ai", "ai", "artificial intelligence",
    "machine learning", "deep learning", "ml engineer", "mlops", "pytorch",
    "time series", "predictive maintenance", "anomaly detection",
    "remaining useful life", "rul", "iot", "signal processing",
    "computer vision", "nlp", "llm", "generative ai",
    "reliability", "condition monitoring", "survival analysis", "prognostics",
]
KEYWORDS_FR = [
    "scientifique des données", "science des données", "analyste de données",
    "ingénieur ia", "ingénieur de recherche", "apprentissage automatique",
    "apprentissage profond", "intelligence artificielle", "maintenance prédictive",
    "ingénieur données",
]
# Disabled by default: Swiss tech job titles are almost always in English.
KEYWORDS_DE = [
    "datenwissenschaftler", "ki ingenieur", "ki entwickler", "maschinelles lernen",
    "künstliche intelligenz", "datenanalyst", "vorausschauende instandhaltung",
    "zustandsüberwachung", "zuverlässigkeit",
]

EXCLUDE_TERMS = [
    "intern", "internship", "stage", "stagiaire", "working student", "werkstudent",
    "trainee", "temporary", "temporaire", "contractor", "freelance",
    "praktikum", "praktikant", "apprentice", "alternance",
]
SENIOR_TERMS = ["senior", "lead", "principal", "head of", "director", "staff", "vp", "manager"]

# Known false positives: always excluded.
HARD_NOISE_TERMS = [
    "site reliability", "sre", "avs/ai", "kantone", "praxis", "support engineer",
    "it support", "business developer", "presales", "pre sales", "recruiter",
]
# Score penalties: the job is still listed, but ranked lower.
SOFT_PENALTY_TERMS = [
    "backend", "frontend", "full stack", "fullstack", "devops", "embedded", "developer",
    "platform engineer", "architect", "consultant", "manager", "software engineer",
]
# Core profile terms: higher base score.
CORE_TERMS = [
    "data scientist", "machine learning", "ml engineer", "research scientist", "research engineer",
    "applied scientist", "predictive maintenance", "condition monitoring", "remaining useful life",
    "survival analysis", "prognostics", "time series", "anomaly detection", "signal processing",
    "deep learning", "ai engineer", "sensors", "sensor", "reliability", "post training", "pre training",
]

REMOTE_TERMS = ["full remote", "fully remote", "100% remote", "remote", "télétravail", "home office", "home-office"]
NON_REMOTE_TERMS = ["hybrid", "hybride", "no remote", "not remote", "on-site", "onsite"]

# Watched companies: one dedicated query per company on jobs.ch and jobup.ch.
WATCHED_COMPANIES = [
    "Hitachi Energy", "ABB", "Siemens", "Holcim", "Lafarge", "Stadler", "SBB",
    "Kanadevia", "Rolex", "CERN",
]
# Score bonus (industrial or R&D employers).
INDUSTRIAL_COMPANIES = WATCHED_COMPANIES + ["RUAG", "Empa", "Sulzer", "Bühler", "Schindler", "Georg Fischer"]
JOBCLOUD_QUERIES = [
    "Data Scientist", "Machine Learning", "Research Engineer", "AI Consultant",
    "Predictive Maintenance", "Condition Monitoring", "Reliability", "Time Series",
    "Applied Scientist",
]
JOBCLOUD_SITES = {
    "jobs.ch": {
        "api": "https://www.jobs.ch/api/v1/public/search",
        "detail": "https://www.jobs.ch/en/vacancies/detail/{id}/",
    },
    "jobup.ch": {
        "api": "https://www.jobup.ch/api/v1/public/search",
        "detail": "https://www.jobup.ch/en/jobs/detail/{id}/",
    },
}

# Greenhouse boards: Zurich-based jobs whose title contains one of title_terms.
GREENHOUSE_BOARDS = [
    {"token": "anthropic", "label": "Anthropic", "locations": ["zurich", "zürich", "switzerland"],
     "title_terms": ["research", "scientist", "training", "machine learning", "rl",
                     "reinforcement", "interpretability", "alignment", "applied", "data"]},
]

# Google Careers (includes DeepMind roles): server-side rendered result pages.
GOOGLE_CAREERS_URL = "https://www.google.com/about/careers/applications/jobs/results"
GOOGLE_LOCATIONS = ["Zurich, Switzerland", "Geneva, Switzerland"]
GOOGLE_MAX_PAGES = 5

# Workday portals: fill in by hand (https://<tenant>.<wd>.myworkdayjobs.com/<site>).
WORKDAY_SITES = [
    # {"label": "Entreprise X", "tenant": "entreprisex", "wd": "wd3", "site": "Careers"},
]
WORKDAY_TERMS = ["data scientist", "machine learning", "reliability", "predictive maintenance"]

# Direct links added at the bottom of the e-mail for manual checking.
CAREER_LINKS = {
    "Anthropic": "https://www.anthropic.com/careers",
    "Google DeepMind": "https://deepmind.google/careers/",
    "Hitachi Energy": "https://www.hitachienergy.com/careers/open-jobs",
    "ABB": "https://careers.abb/",
    "Siemens": "https://jobs.siemens.com/",
    "Holcim": "https://careers.holcim.com/",
    "Stadler": "https://www.stadlerrail.com/en/careers/",
    "SBB": "https://company.sbb.ch/en/jobs-careers.html",
    "CERN": "https://careers.cern/",
}

ZH_CITIES = ("zurich", "zürich", "winterthur", "schlieren", "wallisellen", "dübendorf",
             "dubendorf", "opfikon", "kloten", "glattbrugg", "oerlikon", "adliswil",
             "brüttisellen", "birmensdorf", "uster", "dietikon", "regensdorf", "thalwil",
             "horgen", "bülach", "volketswil", "urdorf", "effretikon")
REGION_CITIES = ("baden", "dättwil", "daettwil", "zug", "baar", "cham", "rotkreuz", "aarau",
                 "lenzburg", "brugg", "turgi", "wettingen", "frauenfeld")
REGION_CANTONS = {"AG", "ZG", "TG", "SH", "SZ"}
GE_CITIES = ("geneva", "genève", "geneve", "genf", "meyrin")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
API_HEADERS = dict(HEADERS, Accept="application/json")

STATS = {}  # per-source diagnostics, shown in the e-mail


# ============================== OUTILS ================================
def _compile(words):
    normalized = {re.sub(r"[\-_/]+", " ", w.lower()) for w in words}
    parts = [re.escape(w) for w in sorted(normalized, key=len, reverse=True)]
    return re.compile(r"(?<![a-zà-ÿ0-9])(?:" + "|".join(parts) + r")(?![a-zà-ÿ0-9])")


KW_RE = _compile(KEYWORDS_EN + KEYWORDS_FR + (KEYWORDS_DE if INCLUDE_GERMAN else []))
EXCLUDE_RE = _compile(EXCLUDE_TERMS)
SENIOR_RE = _compile(SENIOR_TERMS)
HARD_NOISE_RE = _compile(HARD_NOISE_TERMS)
SOFT_RE = _compile(SOFT_PENALTY_TERMS)
CORE_RE = _compile(CORE_TERMS)
# Cities as whole words: avoids 'uster' in 'cluster' or 'cham' in 'Champéry'.
ZH_RE = _compile(ZH_CITIES)
REGION_RE = _compile(REGION_CITIES)
GE_RE = _compile(GE_CITIES)


def clean(text):
    if text is None:
        return ""
    text = str(text).replace("<![CDATA[", "").replace("]]>", "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def norm_title(title):
    """Lowercase, dashes and slashes replaced by spaces ('Data-Scientist (m/w/d)' -> 'data scientist (m w d)')."""
    return re.sub(r"[\-_/]+", " ", clean(title).lower())


def matches_keywords(title):
    """Whole words only: 'ai' does not match 'maintenance', 'ia' does not match 'industrial'."""
    if not title:
        return False
    t = norm_title(title)
    if EXCLUDE_RE.search(t) or HARD_NOISE_RE.search(t):
        return False
    return bool(KW_RE.search(t))


def is_senior(title):
    return bool(SENIOR_RE.search(norm_title(title)))


def is_full_remote(text):
    text_lower = text.lower()
    if any(nr in text_lower for nr in NON_REMOTE_TERMS):
        return False
    return any(rt in text_lower for rt in REMOTE_TERMS)


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_recent(dt):
    return dt is None or dt >= datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_OLD)


def make_job(title, company, location, link, source, canton="", published=None, salary=""):
    return {
        "title": clean(title),
        "company": clean(company) or "N/A",
        "location": clean(location) or "Suisse",
        "canton": (canton or "").strip().upper(),
        "link": (link or "").strip(),
        "source": source,
        "published": published.date().isoformat() if published else "",
        "salary": clean(salary),
        "score": 0,
    }


def http_get(url, **kwargs):
    time.sleep(REQUEST_DELAY)
    headers = kwargs.pop("headers", HEADERS)
    try:
        return requests.get(url, headers=headers, timeout=15, **kwargs)
    except requests.RequestException as exc:
        print(f"  ! request failed {url} : {exc}")
        return None


def set_stats(source, raw=0, kept=0, note=""):
    STATS[source] = {"raw": raw, "kept": kept, "note": note}


# ============================== SOURCES ===============================
def get_swissdevjobs():
    print(f"--- SwissDevJobs ({MAX_DAYS_OLD} dernier(s) jour(s)) ---")
    jobs = []
    r = http_get("https://swissdevjobs.ch/rss")
    if r is None or r.status_code != 200:
        set_stats("SwissDevJobs", note=f"HTTP {getattr(r, 'status_code', 'erreur')}")
        return jobs
    items = re.findall(r"<(?:item|entry)[^>]*>([\s\S]*?)</(?:item|entry)>", r.text, re.IGNORECASE)
    for content in items:
        d_match = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", content, re.IGNORECASE)
        pub = parse_dt(d_match.group(1)) if d_match else None
        if not is_recent(pub):
            continue
        t_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        l_match = re.search(r"<link[^>]*href=[\"'](.*?)[\"']", content, re.IGNORECASE)
        if not l_match:
            l_match = re.search(r"<link[^>]*>(.*?)</link>", content, re.IGNORECASE | re.DOTALL)
        loc_match = re.search(r"<location[^>]*>(.*?)</location>", content, re.IGNORECASE | re.DOTALL)
        title = clean(t_match.group(1)) if t_match else ""
        link = clean(l_match.group(1)) if l_match else ""
        location = clean(loc_match.group(1)) if loc_match else "Suisse"
        if matches_keywords(title) and link:
            company, salary = "Voir offre", ""
            m = re.match(r"^(?P<t>.*?)\s+@\s+(?P<c>.*?)(?:\s+\[(?P<s>[^\]]+)\])?$", title)
            if m:
                title, company, salary = m.group("t"), m.group("c"), m.group("s") or ""
            jobs.append(make_job(title, company, location, link, "SwissDevJobs", published=pub, salary=salary))
    set_stats("SwissDevJobs", len(items), len(jobs))
    return jobs


def _jobcloud_item_to_job(site, cfg, item):
    title = item.get("title", "")
    pub = parse_dt(item.get("publication_date") or item.get("updated_date"))
    if not is_recent(pub) or not matches_keywords(title):
        return None
    job_id = item.get("id") or item.get("job_id")
    if not job_id:
        return None
    company = item.get("company_name")
    if not company and isinstance(item.get("company"), dict):
        company = item["company"].get("name")
    return make_job(title, company or "N/A", item.get("place", "Suisse"),
                    cfg["detail"].format(id=job_id), site, canton=item.get("canton", ""), published=pub)


def get_jobcloud():
    """jobs.ch and jobup.ch: same platform (JobCloud), same response format."""
    print("--- jobs.ch and jobup.ch ---")
    jobs = []
    for site, cfg in JOBCLOUD_SITES.items():
        raw, kept_before, failures = 0, len(jobs), 0
        seen_links = set()
        for term in JOBCLOUD_QUERIES + WATCHED_COMPANIES:
            for page in range(1, JOBCLOUD_MAX_PAGES + 1):
                r = http_get(cfg["api"], params={"query": term, "page": page, "rows": 20}, headers=API_HEADERS)
                if r is None or r.status_code != 200:
                    failures += 1
                    break
                try:
                    documents = r.json().get("documents", [])
                except ValueError:
                    failures += 1
                    break
                if not documents:
                    break
                raw += len(documents)
                for item in documents:
                    job = _jobcloud_item_to_job(site, cfg, item)
                    if job and job["link"] not in seen_links:
                        seen_links.add(job["link"])
                        jobs.append(job)
        note = f"{failures} requête(s) en échec" if failures else ""
        if raw == 0:
            note = (note + " ; aucune donnée reçue, l'API a peut-être changé").strip(" ;")
        set_stats(site, raw, len(jobs) - kept_before, note)
    return jobs


def get_greenhouse():
    """Official Greenhouse API : https://boards-api.greenhouse.io/v1/boards/<token>/jobs"""
    print("--- Greenhouse ---")
    jobs = []
    for board in GREENHOUSE_BOARDS:
        label = board["label"]
        if not board.get("enabled", True):
            set_stats(label, note="disabled (check the board token)")
            continue
        title_re = _compile(board["title_terms"]) if board.get("title_terms") else None
        r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{board['token']}/jobs",
                     params={"content": "false"}, headers=API_HEADERS)
        if r is None or r.status_code != 200:
            set_stats(label, note=f"HTTP {getattr(r, 'status_code', 'erreur')} (token correct ?)")
            continue
        try:
            postings = r.json().get("jobs", [])
        except ValueError:
            set_stats(label, note="non-JSON response")
            continue
        kept = 0
        for p in postings:
            location = (p.get("location") or {}).get("name", "")
            title = p.get("title", "")
            pub = parse_dt(p.get("updated_at"))
            if not is_recent(pub):
                continue
            if not any(loc in location.lower() for loc in board["locations"]):
                continue
            if EXCLUDE_RE.search(norm_title(title)):
                continue
            if title_re and not title_re.search(norm_title(title)):
                continue
            jobs.append(make_job(title, label, location, p.get("absolute_url", ""), "Greenhouse", published=pub))
            kept += 1
        set_stats(label, len(postings), kept)
    return jobs


def get_google_careers():
    """Google Careers: results are embedded as JSON in the page (AF_initDataCallback 'ds:1')."""
    print("--- Google Careers ---")
    jobs, raw, seen = [], 0, set()
    note = ""
    for location in GOOGLE_LOCATIONS:
        for page in range(1, GOOGLE_MAX_PAGES + 1):
            r = http_get(GOOGLE_CAREERS_URL, params={"hl": "en_US", "location": location, "page": page})
            if r is None or r.status_code != 200:
                note = f"HTTP {getattr(r, 'status_code', 'erreur')}"
                break
            m = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", r.text, re.S)
            try:
                postings = json.loads(m.group(1))[0] if m else []
            except (ValueError, IndexError, TypeError):
                note = "format de page changé"
                break
            if not postings:
                if not m:
                    note = "format de page changé"
                break
            for p in postings:
                try:
                    job_id, title, company = str(p[0]), p[1], p[7] or "Google"
                    loc_info = (p[9] or [[location]])[0]
                    loc = loc_info[0]
                    country = loc_info[5] if len(loc_info) > 5 else "CH"
                    ts = (p[12] or [None])[0]
                except (IndexError, TypeError):
                    continue
                raw += 1
                if country != "CH":  # Google mixes jobs from other countries into the results
                    continue
                pub = datetime.fromtimestamp(ts, timezone.utc) if ts else None
                if job_id in seen or not is_recent(pub) or not matches_keywords(title):
                    continue
                seen.add(job_id)
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                jobs.append(make_job(title, company, loc, f"{GOOGLE_CAREERS_URL}/{job_id}-{slug}",
                                     "Google Careers", published=pub))
            if len(postings) < 20:
                break
    set_stats("Google Careers", raw, len(jobs), note)
    return jobs


def get_workday():
    """'cxs' API used by Workday career pages (JSON POST)."""
    print("--- Workday ---")
    jobs = []
    for site in WORKDAY_SITES:
        base = f"https://{site['tenant']}.{site['wd']}.myworkdayjobs.com"
        raw, kept = 0, 0
        for term in WORKDAY_TERMS:
            time.sleep(REQUEST_DELAY)
            try:
                r = requests.post(f"{base}/wday/cxs/{site['tenant']}/{site['site']}/jobs",
                                  json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term},
                                  headers=API_HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                postings = r.json().get("jobPostings", [])
            except (requests.RequestException, ValueError):
                continue
            for p in postings:
                raw += 1
                title = p.get("title", "")
                posted = p.get("postedOn", "")
                m = re.search(r"(\d+)\+?\s+Days? Ago", posted, re.IGNORECASE)
                if m and int(m.group(1)) > MAX_DAYS_OLD:
                    continue
                if matches_keywords(title):
                    jobs.append(make_job(title, site["label"], p.get("locationsText", ""),
                                         f"{base}/en-US/{site['site']}{p.get('externalPath', '')}", "Workday"))
                    kept += 1
        set_stats(site["label"], raw, kept)
    return jobs


def _decode_header(value):
    parts = []
    for chunk, enc in decode_header(value or ""):
        parts.append(chunk.decode(enc or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk)
    return "".join(parts)


def _imap_date(dt):
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year}"


LINKEDIN_LINK_RE = re.compile(
    r'href="(https://[a-z.]*linkedin\.com/(?:comm/)?jobs/view/(\d+)[^"]*)"[^>]*>(.*?)</a>', re.I | re.S)


def parse_linkedin_alert_html(body):
    """Extract (id, title) of the jobs in a LinkedIn alert e-mail."""
    found = {}
    for _url, job_id, inner in LINKEDIN_LINK_RE.findall(body):
        text = clean(inner)
        if len(text) > len(found.get(job_id, "")):
            found[job_id] = text
    return [(job_id, text) for job_id, text in found.items() if len(text) >= 6]


def get_linkedin_alerts():
    """Read LinkedIn alert e-mails from the Gmail inbox. LinkedIn itself is never accessed."""
    print("--- Alertes LinkedIn (IMAP) ---")
    if not LINKEDIN_IMAP:
        set_stats("LinkedIn (alertes)", note="disabled (set LINKEDIN_IMAP=1 to enable)")
        return []
    jobs = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(EMAIL_FROM, EMAIL_PASSWORD)
        imap.select("INBOX")
        since = _imap_date(datetime.now() - timedelta(days=LINKEDIN_DAYS))
        _status, data = imap.search(None, f'(FROM "jobalerts-noreply@linkedin.com" SINCE {since})')
        for num in (data[0] or b"").split():
            _status, msg_data = imap.fetch(num, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            subject = _decode_header(msg.get("Subject", ""))
            date = parse_dt(msg.get("Date"))
            for part in msg.walk():
                if part.get_content_type() != "text/html":
                    continue
                body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                for job_id, title in parse_linkedin_alert_html(body):
                    if EXCLUDE_RE.search(norm_title(title)):
                        continue
                    jobs.append(make_job(title, "Voir LinkedIn", subject,
                                         f"https://www.linkedin.com/jobs/view/{job_id}/", "LinkedIn", published=date))
        imap.logout()
        set_stats("LinkedIn (alertes)", len(jobs), len(jobs))
    except Exception as exc:  # IMAP disabled, wrong password, etc.
        set_stats("LinkedIn (alertes)", note=f"erreur IMAP : {exc}")
    return jobs


# ====================== TRAITEMENT ET RAPPORT =========================
LEGAL_FORMS_RE = re.compile(r"\b(ag|sa|gmbh|sàrl|sarl|ltd|inc|co|kg)\b")


def norm_company(company):
    return re.sub(r"\W+", "", LEGAL_FORMS_RE.sub(" ", company.lower()))


def relevance(job):
    """Relevance score: 3 for core profile terms, 1 otherwise; senior and noise penalties; industrial bonus."""
    t = norm_title(job["title"])
    score = 3 if CORE_RE.search(t) else 1
    if SOFT_RE.search(t):
        score -= 1
    if is_senior(job["title"]):
        score -= 1
    company = job["company"].lower()
    if any(re.search(r"\b" + re.escape(c.lower()) + r"\b", company) for c in INDUSTRIAL_COMPANIES):
        score += 1
    return score


def dedupe(jobs):
    """Same title and same company (legal forms and postcodes ignored) = duplicate,
    including across jobs.ch and jobup.ch."""
    seen_links, seen_keys, unique = set(), {}, []
    for job in jobs:
        if not job["link"] or job["link"] in seen_links:
            continue
        key = (re.sub(r"\W+", "", job["title"].lower()), norm_company(job["company"]))
        loc = re.sub(r"\W+", "", re.sub(r"\d+", "", job["location"].lower()))
        known = seen_keys.setdefault(key, [])
        if any(p == loc or "suisse" in (p, loc) or "" in (p, loc) for p in known):
            continue
        known.append(loc)
        seen_links.add(job["link"])
        unique.append(job)
    return unique


def categorize_jobs(jobs):
    categories = {"ZH": [], "REGION": [], "GE": [], "Full_Remote": [], "Reste_CH": []}
    for job in jobs:
        canton = job.get("canton", "")
        loc_text = f"{job.get('location', '')} {job.get('title', '')}".lower()
        if canton == "ZH" or ZH_RE.search(loc_text):
            categories["ZH"].append(job)
        elif canton in REGION_CANTONS or REGION_RE.search(loc_text):
            categories["REGION"].append(job)
        elif canton == "GE" or GE_RE.search(loc_text):
            categories["GE"].append(job)
        elif is_full_remote(loc_text):
            categories["Full_Remote"].append(job)
        else:
            categories["Reste_CH"].append(job)
    return categories


def build_table(job_list):
    if not job_list:
        return "<p><i>Aucune nouvelle offre dans cette catégorie aujourd'hui.</i></p>"
    rows = ["<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse; width:100%; margin-bottom:20px;'>",
            "<tr style='background-color:#f2f2f2;'><th></th><th>★</th><th>Titre</th><th>Entreprise</th>"
            "<th>Lieu</th><th>Source</th><th>Date</th></tr>"]
    for j in sorted(job_list, key=lambda x: (x.get("score", 0), x["published"] or ""), reverse=True):
        link = j["link"] if j["link"].startswith(("http://", "https://")) else "#"
        badge = "⚠ senior" if is_senior(j["title"]) else ""
        salary = f" <small>[{html.escape(j['salary'])}]</small>" if j.get("salary") else ""
        rows.append(
            f"<tr><td>{badge}</td><td>{j.get('score', 0)}</td>"
            f"<td><a href='{html.escape(link, quote=True)}' target='_blank'><b>{html.escape(j['title'])}</b></a>{salary}</td>"
            f"<td>{html.escape(j['company'])}</td><td>{html.escape(j['location'])}</td>"
            f"<td><small>{html.escape(j['source'])}</small></td><td><small>{html.escape(j['published'])}</small></td></tr>")
    rows.append("</table>")
    return "".join(rows)


def build_html(categorized, total_count, priority=None):
    out = [f"<h1>Alerte Data / AI : {total_count} nouvelles offres ({MAX_DAYS_OLD * 24}h)</h1>"]
    if total_count == 0:
        out.append("<p>Aucune nouvelle offre CDI correspondante sur cette période.</p>")
    if priority:
        out.append(f"<h2 style='color:#c01c28;'>★ À regarder en priorité : {len(priority)} offres</h2>")
        out.append(build_table(priority))
    if total_count:
        sections = [
            ("ZH", "1. Zurich (ZH)", "#1a5fb4"),
            ("REGION", "2. Région de Zurich (Baden, Zug, Argovie, Thurgovie)", "#26a269"),
            ("GE", "3. Genève (GE)", "#2ec27e"),
            ("Full_Remote", "4. Full Remote", "#9141ac"),
            ("Reste_CH", "5. Reste de la Suisse", "#e5a50a"),
        ]
        for key, label, color in sections:
            out.append(f"<h2 style='color:{color};'>{label} : {len(categorized[key])} offres</h2>")
            out.append(build_table(categorized[key]))
    out.append("<h3>Liens directs à vérifier</h3><p>")
    out.append(" | ".join(f"<a href='{html.escape(u, quote=True)}'>{html.escape(n)}</a>" for n, u in CAREER_LINKS.items()))
    out.append("</p><h3>Diagnostic des sources</h3><table border='1' cellpadding='4' cellspacing='0' "
               "style='border-collapse:collapse;'><tr><th>Source</th><th>Lues</th><th>Retenues</th><th>Note</th></tr>")
    for name, s in STATS.items():
        out.append(f"<tr><td>{html.escape(name)}</td><td>{s['raw']}</td><td>{s['kept']}</td>"
                   f"<td>{html.escape(s['note'])}</td></tr>")
    out.append("</table>")
    return "".join(out)


def send_email(html_body, total_count):
    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASSWORD):
        raise ValueError("Les secrets EMAIL_FROM, EMAIL_TO ou EMAIL_PASSWORD sont manquants !")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Alertes Emploi Data/AI CH ({total_count} nouvelles offres)"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f"✅ Alerte quotidienne de {total_count} offres envoyée avec succès !")


SOURCES = [
    ("SwissDevJobs", get_swissdevjobs),
    ("jobs.ch / jobup.ch", get_jobcloud),
    ("Greenhouse", get_greenhouse),
    ("Google Careers", get_google_careers),
    ("Workday", get_workday),
    ("LinkedIn (alertes)", get_linkedin_alerts),
]


def collect():
    raw_jobs = []
    for name, fn in SOURCES:
        try:
            raw_jobs.extend(fn())
        except Exception as exc:  # a failing source does not block the others
            print(f"  ! {name} crashed : {exc}")
            set_stats(name, note=f"plantage : {exc}")
    jobs = dedupe(raw_jobs)
    for job in jobs:
        job["score"] = relevance(job)
    return jobs


def main(argv):
    jobs = collect()
    categorized = categorize_jobs(jobs)
    priority = sorted((j for j in jobs if j["score"] >= PRIORITY_MIN_SCORE),
                      key=lambda j: (j["score"], j["published"]), reverse=True)[:30]
    html_body = build_html(categorized, len(jobs), priority)
    if "--dry-run" in argv:
        Path("rapport.html").write_text(html_body, encoding="utf-8")
        print(f"Mode test : {len(jobs)} offres écrites dans rapport.html")
        return
    send_email(html_body, len(jobs))


if __name__ == "__main__":
    main(sys.argv[1:])
