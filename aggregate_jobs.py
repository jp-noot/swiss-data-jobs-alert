import os
import smtplib
import requests
import re
from datetime import datetime, timedelta, timezone
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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    jobs = []
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            items = re.findall(r'<(?:item|entry)[^>]*>([\s\S]*?)</(?:item|entry)>', response.text, re.IGNORECASE)
            
            for content in items:
                d_match = re.search(r'<pubDate[^>]*>(.*?)</pubDate>', content, re.IGNORECASE)
                if d_match:
                    try:
                        pub_date = parsedate_to_datetime(d_match.group(1))
                        if datetime.now(pub_date.tzinfo) - pub_date > timedelta(days=MAX_DAYS_OLD):
                            continue
                    except:
                        pass

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
                        "location": "Suisse / Remote",
                        "canton": "",
                        "link": link,
                        "source": "SwissDevJobs"
                    })
    except Exception as e:
        print(f"Erreur SwissDevJobs: {e}")
        
    return jobs

def get_job_ch():
    url = "https://www.jobs.ch/api/v1/public/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    jobs = []
    query_terms = ["Data Scientist", "Machine Learning", "Research Engineer", "AI Consultant"]
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_OLD)

    for term in query_terms:
        for page in range(1, 3):  # 2 pages suffisent amplement pour les dernières 24h
            params = {
                "query": term,
                "page": page,
                "rows": 20
            }
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    documents = data.get("documents", [])
                    if not documents:
                        break
                    
                    for item in documents:
                        title = item.get("title", "")
                        
                        pub_str = item.get("publication_date") or item.get("updated_date")
                        if pub_str:
                            try:
                                pub_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                                if pub_date < cutoff_date:
                                    continue
                            except Exception:
                                pass
                        
                        if matches_keywords(title):
                            job_id = item.get("job_id")
                            company = item.get("company_name", "N/A")
                            city = item.get("place", "Suisse")
                            canton = item.get("canton", "")
                            # Lien corrigé avec /detail/ pour éviter les 404
                            link = item.get("url") or f"https://www.jobs.ch/fr/offres/detail/{job_id}/"
                            
                            jobs.append({
                                "title": title,
                                "company": company,
                                "location": city,
                                "canton": canton,
                                "link": link,
                                "source": "job.ch"
                            })
            except Exception as e:
                print(f"Erreur job.ch ({term} - p.{page}): {e}")
                
    return jobs

def build_table(job_list):
    if not job_list: return "<p><i>Aucune nouvelle offre trouvée dans cette région.</i></p>"
    html = "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse; width:100%; margin-bottom:20px;'>"
    html += "<tr style='background-color:#f2f2f2;'><th>Titre</th><th>Entreprise</th><th>Lieu</th><th>Source</th></tr>"
    for j in job_list:
        html += f"<tr><td><a href='{j['link']}' target='_blank'><b>{j['title']}</b></a></td><td>{j['company']}</td><td>{j['location']}</td><td><small>{j['source']}</small></td></tr>"
    html += "</table>"
    return html

def categorize_jobs(jobs):
    categories = {"ZH": [], "GE": [], "Reste_CH": []}
    for job in jobs:
        canton = (job.get("canton", "") or "").upper()
        title_loc = (job.get("location", "") or "") + " " + (job.get("title", "") or "")
        
        if canton == "ZH" or "zurich" in title_loc.lower() or "zürich" in title_loc.lower():
            categories["ZH"].append(job)
        elif canton == "GE" or "geneva" in title_loc.lower() or "genève" in title_loc.lower():
            categories["GE"].append(job)
        else:
            categories["Reste_CH"].append(job)
    return categories

def send_daily_email(categorized, total_count):
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")
    email_password = os.environ.get("EMAIL_PASSWORD")

    if not email_from or not email_to or not email_password:
        raise ValueError("Les secrets EMAIL_FROM, EMAIL_TO ou EMAIL_PASSWORD sont manquants !")

    if total_count == 0:
        html = "<p>Aucune nouvelle offre CDI correspondante dans les dernières 24h.</p>"
    else:
        html = f"<h1>Alerte Quotidienne Data / AI - {total_count} nouvelles offres (24h)</h1>"
        html += f"<h2 style='color:#1a5fb4;'>1. Zurich (ZH) - {len(categorized['ZH'])} offres</h2>" + build_table(categorized['ZH'])
        html += f"<h2 style='color:#2ec27e;'>2. Genève (GE) - {len(categorized['GE'])} offres</h2>" + build_table(categorized['GE'])
        html += f"<h2 style='color:#e5a50a;'>3. Reste de la Suisse & Remote - {len(categorized['Reste_CH'])} offres</h2>" + build_table(categorized['Reste_CH'])

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f" Alertes Emploi Data/AI CH ({total_count} offres)"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email_from, email_password)
        server.sendmail(email_from, email_to, msg.as_string())
    print(f"✅ Alerte quotidienne de {total_count} offres envoyée avec succès !")

if __name__ == "__main__":
    raw_jobs = []
    
    # 1. Collecte SwissDevJobs
    raw_jobs.extend(get_swissdevjobs())
    
    # 2. Collecte job.ch
    raw_jobs.extend(get_job_ch())
    
    # Déduplication par lien d'offre
    seen = set()
    unique_jobs = [j for j in raw_jobs if not (j["link"] in seen or seen.add(j["link"]))]
    
    # Catégorisation régionale & Envoi
    categorized = categorize_jobs(unique_jobs)
    send_daily_email(categorized, len(unique_jobs))