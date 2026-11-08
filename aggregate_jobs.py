import os
import requests
import xml.etree.ElementTree as ET
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

KEYWORDS = [
    # Data Science & Recherche
    "data scientist", "data-scientist", "data science", "data-science", 
    "data analyst", "data engineer",
    "research scientist", "research-scientist", "research engineer",
    # IA & Machine Learning
    "ai", "artificial intelligence", "ia",
    "machine learning", "deep learning", 
    "ml engineer", "mlop", "mlops",
    # Spécialités
    "time series", "time-series",
    "predictive maintenance", "maintenance prédictive",
    "computer vision", "nlp", "llm", "generative ai"
]

CANTONS = ["ZH"]
MAX_DAYS_OLD = 1

def get_jobroom_jobs():
    url = "https://www.job-room.ch/api/v1/jobs/search"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    payload = {"keywords": KEYWORDS, "cantons": CANTONS, "onlineSince": MAX_DAYS_OLD, "sort": "DATE_DESC"}
    jobs = []
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            for item in response.json().get("result", []):
                jobs.append({
                    "title": item.get("title"),
                    "company": item.get("company", {}).get("name", "Confidentiel"),
                    "location": item.get("location", {}).get("city", "Suisse"),
                    "link": item.get("url") or f"https://www.job-room.ch/job-ad/{item.get('id')}",
                    "source": "Job-Room"
                })
    except Exception as e:
        print(f"Erreur Job-Room: {e}")
    return jobs

def get_swissdevjobs():
    url = "https://swissdevjobs.ch/feed.xml"
    jobs = []
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for item in root.findall("./channel/item"):
                title = item.find("title").text
                link = item.find("link").text
                if any(kw.lower() in title.lower() for kw in KEYWORDS):
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
    if not jobs:
        html = "<p>Aucune nouvelle offre aujourd'hui.</p>"
    else:
        html = f"<h2>{len(jobs)} Nouvelles offres en Suisse</h2><ul>"
        for j in jobs:
            html += f"<li><a href='{j['link']}'><b>{j['title']}</b></a> - {j['company']} ({j['location']}) [{j['source']}]</li>"
        html += "</ul>"

    message = Mail(
        from_email=os.environ.get("EMAIL_FROM"),
        to_emails=os.environ.get("EMAIL_TO"),
        subject=f" Alertes Emploi CH ({len(jobs)} offres)",
        html_content=html
    )
    sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))
    sg.send(message)
    print("Mail envoyé avec succès !")

if __name__ == "__main__":
    raw_jobs = get_jobroom_jobs() + get_swissdevjobs()
    seen = set()
    unique_jobs = [j for j in raw_jobs if not (j["link"] in seen or seen.add(j["link"]))]
    send_daily_email(unique_jobs)
