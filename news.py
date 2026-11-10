import datetime, html, time
from google import genai
from google.genai import types

client = genai.Client()  # liest GEMINI_API_KEY aus der Umgebung
MODEL = "gemini-3.8-flash"  # falls veraltet: aktuellen Flash-Namen in AI Studio nachsehen
KATEGORIEN = ["Welt", "Deutschland", "Tech & KI", "Wirtschaft", "Gaming & Roblox"]
heute = datetime.date.today().strftime("%d.%m.%Y")

def render(text):
    zeilen = [z.strip().lstrip("-*• ").replace("**", "") for z in text.splitlines() if z.strip()]
    return "<ul>" + "".join(f"<li>{html.escape(z)}</li>" for z in zeilen) + "</ul>"

teile = []
for kat in KATEGORIEN:
    try:
        r = client.models.generate_content(
            model=MODEL,
            contents=(
                f"Suche die wichtigsten News von heute ({heute}) zum Bereich '{kat}'. "
                "Antworte auf Deutsch mit genau 4 Stichpunkten, jeder max. 1 Satz. "
                "Jede Zeile beginnt mit '- '. Keine Einleitung, keine Quellenangaben."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        inhalt = render(r.text or "Keine Ergebnisse.")
    except Exception as e:
        inhalt = f"<p>Fehler: {html.escape(str(e))}</p>"
    teile.append(f"<h2>{kat}</h2>{inhalt}")
    time.sleep(10)  # schont das Rate-Limit im Gratis-Tarif

page = f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:sans-serif;background:#111;color:#eee;padding:16px;max-width:700px;margin:auto}}
h2{{color:#7cc4ff;margin-bottom:4px}} li{{margin:6px 0}}
</style>
<h1>News {heute}</h1>{''.join(teile)}"""
open("index.html", "w", encoding="utf-8").write(page)
