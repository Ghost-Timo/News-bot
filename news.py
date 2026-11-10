import calendar, datetime, html, re, time
import feedparser
from google import genai

MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]  # zweites ist Fallback (Name nach Muster geraten)
STUNDEN = 36  # nur Meldungen der letzten X Stunden
FEEDS = {
    "Welt": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Deutschland": "https://www.tagesschau.de/inland/index~rss2.xml",
    "Tech & KI": "https://www.heise.de/rss/heise-atom.xml",
    "Wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "Gaming & Roblox": "https://news.google.com/rss/search?q=Roblox&hl=de&gl=DE&ceid=DE:de",
}
heute = datetime.date.today().strftime("%d.%m.%Y")

def sauber(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()

# 1) Alle Meldungen der letzten STUNDEN holen
roh = {}
for kat, url in FEEDS.items():
    gesehen, liste = set(), []
    for e in feedparser.parse(url).entries:
        titel = sauber(e.get("title"))
        if not titel or titel in gesehen:
            continue
        p = e.get("published_parsed") or e.get("updated_parsed")
        if p and time.time() - calendar.timegm(p) > STUNDEN * 3600:
            continue
        text = sauber(e.get("summary"))
        if len(text) < 20 or titel[:30] in text:
            text = ""
        gesehen.add(titel)
        liste.append({"titel": titel, "text": text[:250], "link": e.get("link", "")})
    roh[kat] = liste

# 2) Gemini: "Das Wichtigste" (eine Anfrage)
prompt = (
    "Wähle aus diesen Schlagzeilen die 6 wichtigsten des Tages über alle Kategorien. "
    "Schreibe je einen Stichpunkt auf Deutsch (englische Meldungen übersetzen), "
    "max. 1 kurzer Satz. Jede Zeile beginnt mit '- '. Keine Einleitung, kein Abschluss.\n\n"
)
for kat, items in roh.items():
    prompt += f"[{kat}]\n" + "\n".join(f"- {i['titel']}" for i in items) + "\n\n"

client = genai.Client()
top, fehler = [], []
for modell in MODELS:
    try:
                r = client.models.generate_content(model=modell, contents=prompt)
        top = [
            z.strip().lstrip("-*• ").replace("**", "")
            for z in (r.text or "").splitlines()
            if z.strip().startswith(("-", "*", "•"))
        ]
        if top:
            break
        fehler.append(f"{modell}: leere Antwort")
    except Exception as ex:
        fehler.append(f"{modell}: {str(ex)[:300]}")
        print("Gemini-Fehler:", modell, ex)

# 3) Seite bauen
def eintrag(i):
    t = html.escape(i["titel"])
    if i["link"]:
        t = f'<a href="{html.escape(i["link"], quote=True)}" target="_blank" rel="noopener">{t}</a>'
    text = f'<div class="t">{html.escape(i["text"])}</div>' if i["text"] else ""
    return f"<li>{t}{text}</li>"

if top:
    oben = "<ul>" + "".join(f"<li>{html.escape(p)}</li>" for p in top) + "</ul>"
else:
    oben = ("<p><i>Zusammenfassung nicht verfügbar.</i></p><p class='err'>"
            + html.escape(" | ".join(fehler)) + "</p>")

unten = ""
for kat, items in roh.items():
    liste = "".join(eintrag(i) for i in items) or "<li>Keine neuen Meldungen.</li>"
    unten += f"<h3>{html.escape(kat)}</h3><ul>{liste}</ul>"

CSS = """
body{font-family:sans-serif;background:#111;color:#eee;padding:16px;max-width:760px;margin:auto;line-height:1.4}
h2{color:#7cc4ff;margin:0 0 6px} h3{color:#7cc4ff;margin:22px 0 4px}
.top{background:#1c2733;border-radius:12px;padding:14px 16px;margin:12px 0 24px}
.top li{margin:8px 0;font-size:1.05em}
li{margin:8px 0} a{color:#eee;text-decoration:none} a:hover{text-decoration:underline}
.t{color:#999;font-size:.85em;margin-top:2px} .err{color:#e88;font-size:.8em}
"""

page = (
    '<!doctype html><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<style>{CSS}</style><h1>News {heute}</h1>"
    f'<div class="top"><h2>Das Wichtigste</h2>{oben}</div>'
    f"<h2>Alle Meldungen</h2>{unten}"
)
open("index.html", "w", encoding="utf-8").write(page)
