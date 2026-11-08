import os
import smtplib
import requests
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

KEYWORDS = [
    "data scientist", "data-scientist", "data science", "data-science", 
    "data analyst", "data engineer",
    "research scientist", "research-scientist", "research engineer",
    "ai", "artificial intelligence", "ia",
    "machine learning", "deep learning", 
    "ml engineer", "mlop", "mlops",
    "time series", "time-series",
    "predictive maintenance", "maintenance prédictive",
    "computer vision", "nlp", "llm", "generative ai"
]

CANTONS = ["ZH"]
MAX_DAYS_OLD = 1

def matches_keywords(text):
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in KEYWORDS)

def get_jobroom_jobs():
    url = "https://www.job-room.ch/api/v1/jobs/search"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    payload = {
        "keywords": KEYWORDS,
        "cantons": CANTONS,
        "onlineSince": MAX_DAYS_OLD,
        "sort": "DATE_DESC"
    }
    jobs = []
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            for item in response.json().get("result", []):
                title = item.get("title", "")
                if matches_keywords(title):
                    jobs.append({
                        "title": title,
                        "company": item.get("company", {}).get("name", "Entreprise confidentielle"),
                        "location": item.get("location", {}).get("city", "Zurich / CH"),
                        "link": item.get("url") or f"https://www.job-room.ch/job-ad/{item.get('id')}",
                        "source": "Job-Room (Arbeit.swiss)"
                    })
    except Exception as e:
        print(f"Erreur Job-Room API: {e}")
    return jobs

def get_swissdevjobs():
    url = "https://swissdevjobs.ch/feed.xml"
    jobs = []
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            content = response.content.decode('utf-8', errors='ignore')
            root = ET.fromstring(content)
            for item in root.findall("./channel/item"):
                title_elem = item.find("title")
                link_elem = item.find("link")
                title = title_elem.text if title_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                
                if matches_keywords(title):
                    jobs.append({
                        "title": title,
                        "company": "Voir offre",
                        "location": "Zurich / Remote",
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
        html = "<p>Aucune nouvelle offre correspondant à vos critères aujourd'hui.</p>"
    else:
        html = f"<h2>{len(jobs)} Nouvelles offres Data / AI en Suisse</h2>"
        html += "<table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse; width:100%;'>"
        html += "<tr style='background-color:#f2f2f2;'><th>Titre</th><th>Entreprise</th><th>Lieu</th><th>Source</th></tr>"
        for j in jobs:
            html += f"""
            <tr>
                <td><a href='{j['link']}' target='_blank'><b>{j['title']}</b></a></td>
                <td>{j['company']}</td>
                <td>{j['location']}</td>
                <td><small>{j['source']}</small></td>
            </tr>
            """
        html += "</table>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Alertes Emploi Data/AI CH ({len(jobs)} offres)"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email_from, email_password)
        server.sendmail(email_from, email_to, msg.as_string())

    print("E-mail envoyé avec succès via Gmail SMTP !")

if __name__ == "__main__":
    raw_jobs = get_jobroom_jobs() + get_swissdevjobs()
    seen = set()
    unique_jobs = [j for j in raw_jobs if not (j["link"] in seen or seen.add(j["link"]))]
    send_daily_email(unique_jobs)
