import os
import smtplib
import requests
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

KEYWORDS = [
    "data scientist", "data-scientist", "data science", "data-science", 
    "data analyst", "data engineer", "research scientist", "research engineer",
    "ai engineer", "ai consultant", "industrial ai", "ai", "artificial intelligence", "ia",
    "machine learning", "deep learning", "ml engineer", "mlop", "mlops", "pytorch",
    "time series", "time-series", "predictive maintenance", "maintenance prédictive",
    "anomaly detection", "remaining useful life", "rul", "iot", "signal processing",
    "computer vision", "nlp", "llm", "generative ai"
]

EXCLUDE_TERMS = ["intern", "internship", "stage", "stagiaire", "working student", "werkstudent", "trainee", "temporary", "temporaire", "contractor", "freelance"]

MAX_DAYS_OLD = 1  # 24 heures pour l'alerte quotidienne

def matches_keywords(title):
    if not title: return False
    title_lower = title.lower()
    if any(ex in title_lower for ex in EXCLUDE_TERMS): return False
    return any(kw in title_lower for kw in KEYWORDS)

def get_swissdevjobs():
    url = "https://swissdevjobs.ch/rss"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    jobs = []
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            items = re.findall(r'<(?:item|entry)[^>]*>([\s\S]*?)</(?:item|entry)>', response.text, re.IGNORECASE)
            
            for content in items:
                # Filtrage par date de publication (24h)
                d_match = re.search(r'<pubDate[^>]*>(.*?)</pubDate>', content, re.IGNORECASE)
                if d_match:
                    try:
                        pub_date = parsedate_to_datetime(d_match.group(1))
                        # Si l'offre a plus de 24h, on passe à la suivante
                        if datetime.now(pub_date.tzinfo) - pub_date > timedelta(days=MAX_DAYS_OLD):
                            continue
                    except:
                        pass # En cas d'erreur de format de date, on garde l'offre par sécurité

                t_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
                l_match = re.search(r'<link[^>]*href=["\'](.*?)["\']', content, re.IGNORECASE)
                if not l_match:
                    l_match = re.search(r'<link[^>]*>(.*?)</link>', content, re.IGNORECASE | re.DOTALL)
                
                title = t_match.group(1).replace("<![CDATA[", "").replace("]]>", "").strip() if t_match else ""
                link = l_match.group(1).replace("<![CDATA[", "").replace("]]>", "").strip() if l_match else ""
                
                if matches_keywords(title):
                    jobs.append({
                        "title": title,
                        "company": "Voir offre",
                        "location": "Suisse",
                        "link": link,
                        "source": "SwissDevJobs"
                    })
    except Exception as e:
        print(f"Erreur SwissDevJobs: {e}")
        
    return jobs

def send_daily_email(jobs):
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")
    email_password = os.environ.get("EMAIL_PASSWORD")

    if not email_from or not email_to or not email_password:
        raise ValueError("Les secrets EMAIL_FROM, EMAIL_TO ou EMAIL_PASSWORD sont manquants !")

    if not jobs:
        html = "<p>Aucune nouvelle offre CDI correspondante dans les dernières 24h.</p>"
    else:
        html = f"<h2>{len(jobs)} Nouvelles offres CDI Data / AI en Suisse</h2>"
        html += "<table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse; width:100%;'>"
        html += "<tr style='background-color:#f2f2f2;'><th>Titre</th><th>Entreprise</th><th>Lieu</th><th>Source</th></tr>"
        for j in jobs:
            html += f"<tr><td><a href='{j['link']}' target='_blank'><b>{j['title']}</b></a></td><td>{j['company']}</td><td>{j['location']}</td><td><small>{j['source']}</small></td></tr>"
        html += "</table>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f" Alertes Emploi Data/AI CH ({len(jobs)} offres)"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email_from, email_password)
        server.sendmail(email_from, email_to, msg.as_string())

if __name__ == "__main__":
    # Job-Room est temporairement désactivé suite au blocage 401
    raw_jobs = get_swissdevjobs()
    
    seen = set()
    unique_jobs = [j for j in raw_jobs if not (j["link"] in seen or seen.add(j["link"]))]
    send_daily_email(unique_jobs)
