import datetime, html, feedparser
from google import genai

MODEL = "gemini-3.8-flash"
FEEDS = {
    "Welt": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Deutschland": "https://www.tagesschau.de/inland/index~rss2.xml",
    "Tech & KI": "https://www.heise.de/rss/heise-atom.xml",
    "Wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "Gaming & Roblox": "https://news.google.com/rss/search?q=Roblox&hl=de&gl=DE&ceid=DE:de",
}
heute = datetime.date.today().strftime("%d.%m.%Y")

# 1) Schlagzeilen holen
roh = {}
for kat, url in FEEDS.items():
    eintraege = feedparser.parse(url).entries[:8]
    roh[kat] = [e.get("title", "").strip() for e in eintraege if e.get("title")]

# 2) Eine einzige Anfrage an Gemini
prompt = "Fasse diese Schlagzeilen pro Kategorie auf Deutsch zusammen: genau 4 Stichpunkte je Kategorie, " \
         "je max. 1 Satz, nur das Wichtigste. Format: Zeile '## Kategorie', darunter Zeilen mit '- '. " \
         "Keine Einleitung.\n\n"
for kat, titel in roh.items():
    prompt += f"## {kat}\n" + "\n".join(f"- {t}" for t in titel) + "\n\n"

abschnitte = {}
try:
    r = genai.Client().models.generate_content(model=MODEL, contents=prompt)
    aktuell = None
    for zeile in (r.text or "").splitlines():
        zeile = zeile.strip()
        if zeile.startswith("##"):
            aktuell = zeile.lstrip("# ").strip()
            abschnitte[aktuell] = []
        elif zeile and aktuell:
            abschnitte[aktuell].append(zeile.lstrip("-*• ").replace("**", ""))
except Exception as e:
    print("Gemini-Fehler:", e)

# 3) Fallback: rohe Schlagzeilen, falls Gemini nichts geliefert hat
if not any(abschnitte.values()):
    abschnitte = {k: v[:4] for k, v in roh.items()}
    hinweis = "<p><i>Zusammenfassung nicht verfügbar, zeige Schlagzeilen.</i></p>"
else:
    hinweis = ""

teile = []
for kat, punkte in abschnitte.items():
    li = "".join(f"<li>{html.escape(p)}</li>" for p in punkte)
    teile.append(f"<h2>{html.escape(kat)}</h2><ul>{li}</ul>")

page = f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:sans-serif;background:#111;color:#eee;padding:16px;max-width:700px;margin:auto}}
h2{{color:#7cc4ff;margin-bottom:4px}} li{{margin:6px 0}}
</style>
<h1>News {heute}</h1>{hinweis}{''.join(teile)}"""
open("index.html", "w", encoding="utf-8").write(page)
