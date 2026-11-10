import calendar, datetime, html, json, re, time
import feedparser
from google import genai
from google.genai import types

MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]
STUNDEN = 48          # Vorfilter: nur Meldungen der letzten X Stunden
TOP_ANZAHL = 5        # Meldungen im Kasten "Das Wichtigste"
PRO_KATEGORIE = 3     # Meldungen pro Kategorie unten
MAX_KANDIDATEN = 20   # so viele Meldungen pro Kategorie sieht Gemini
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

# 1) Meldungen holen
alle, roh = [], {}
for kat, url in FEEDS.items():
    gesehen, liste = set(), []
    for e in feedparser.parse(url).entries:
        titel = sauber(e.get("title"))
        if not titel or titel in gesehen:
            continue
        if kat == "Gaming & Roblox" and re.search(r"\bcodes?\b", titel, re.I):
            continue
        p = e.get("published_parsed") or e.get("updated_parsed")
        if p and time.time() - calendar.timegm(p) > STUNDEN * 3600:
            continue
        text = sauber(e.get("summary"))
        if len(text) < 20 or titel[:30] in text:
            text = ""
        gesehen.add(titel)
        item = {"id": len(alle), "kat": kat, "titel": titel,
                "text": text[:300], "link": e.get("link", "")}
        alle.append(item)
        liste.append(item)
        if len(liste) >= MAX_KANDIDATEN:
            break
    roh[kat] = liste

by_id = {i["id"]: i for i in alle}

def holen(x):
    try:
        return by_id.get(int(x))
    except Exception:
        return None

# 2) Gemini waehlt aus und schreibt die Zusammenfassungen
prompt = (
    "Du bekommst nummerierte Nachrichten-Meldungen (Titel und Kurztext aus RSS-Feeds). Aufgaben:\n"
    f"1) 'top': Waehle die {TOP_ANZAHL} wichtigsten Meldungen des Tages ueber alle Kategorien. "
    "Pro Meldung: 'id', 'titel' (Deutsch, max. 8 Woerter) und 'text' (Deutsch, ca. 3 Saetze, "
    "die das Thema gut erklaeren). Nutze nur Informationen aus Titel und Kurztext, erfinde "
    "nichts dazu; gibt die Quelle wenig her, schreibe lieber kuerzer.\n"
    f"2) 'auswahl': Nenne pro Kategorie die ids der bis zu {PRO_KATEGORIE} wichtigsten Meldungen, "
    "wichtigste zuerst.\n"
    'Antworte nur mit JSON im Format {"top":[{"id":0,"titel":"","text":""}],"auswahl":{"Welt":[0]}}\n\n'
)
for kat, items in roh.items():
    prompt += f"[{kat}]\n"
    for i in items:
        prompt += f"{i['id']}: {i['titel']}" + (f" - {i['text']}" if i["text"] else "") + "\n"
    prompt += "\n"

client = genai.Client()
daten, fehler = None, []
for modell in MODELS:
    try:
        r = client.models.generate_content(
            model=modell,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        t = re.sub(r"^```(?:json)?|```$", "", (r.text or "").strip()).strip()
        daten = json.loads(t)
        if not isinstance(daten, dict):
            raise ValueError("Antwort ist kein JSON-Objekt")
        break
    except Exception as ex:
        daten = None
        fehler.append(f"{modell}: {str(ex)[:300]}")
        print("Gemini-Fehler:", modell, ex)

top, auswahl = [], {}
if daten:
    for t in daten.get("top", [])[:TOP_ANZAHL]:
        i = holen(t.get("id"))
        top.append({
            "titel": t.get("titel") or (i["titel"] if i else ""),
            "text": t.get("text", ""),
            "link": i["link"] if i else "",
        })
    for kat in roh:
        ids = (daten.get("auswahl") or {}).get(kat, [])
        gewaehlt = [holen(x) for x in ids]
        auswahl[kat] = [g for g in gewaehlt if g and g["kat"] == kat][:PRO_KATEGORIE]
for kat in roh:
    if not auswahl.get(kat):
        auswahl[kat] = roh[kat][:PRO_KATEGORIE]

# 3) Seite bauen
def verlinkt(titel, link):
    t = html.escape(titel)
    if link:
        t = f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">{t}</a>'
    return t

if top:
    oben = "<ul>" + "".join(
        f'<li><b>{verlinkt(t["titel"], t["link"])}</b><div class="s">{html.escape(t["text"])}</div></li>'
        for t in top
    ) + "</ul>"
else:
    oben = ("<p><i>Zusammenfassung nicht verfuegbar.</i></p><p class='err'>"
            + html.escape(" | ".join(fehler)) + "</p>")

unten = ""
for kat, items in auswahl.items():
    liste = "".join(
        f'<li>{verlinkt(i["titel"], i["link"])}'
        + (f'<div class="t">{html.escape(i["text"])}</div>' if i["text"] else "")
        + "</li>"
        for i in items
    ) or "<li>Keine neuen Meldungen.</li>"
    unten += f"<h3>{html.escape(kat)}</h3><ul>{liste}</ul>"

CSS = """
body{font-family:sans-serif;background:#111;color:#eee;padding:16px;max-width:760px;margin:auto;line-height:1.45}
h2{color:#7cc4ff;margin:0 0 6px} h3{color:#7cc4ff;margin:22px 0 4px}
.top{background:#1c2733;border-radius:12px;padding:14px 16px;margin:12px 0 24px}
.top li{margin:14px 0;font-size:1.05em} .s{color:#ccc;font-size:.92em;margin-top:3px}
li{margin:8px 0} a{color:#eee;text-decoration:none} a:hover{text-decoration:underline}
.t{color:#999;font-size:.85em;margin-top:2px} .err{color:#e88;font-size:.8em}
"""

page = (
    '<!doctype html><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<style>{CSS}</style><h1>News {heute}</h1>"
    f'<div class="top"><h2>Das Wichtigste</h2>{oben}</div>'
    f"<h2>Weitere Meldungen</h2>{unten}"
)
open("index.html", "w", encoding="utf-8").write(page)

# 4) Kurze Textdatei fuers Widget
if top:
    zeilen = [t["titel"] for t in top]
else:
    zeilen = [items[0]["titel"] for items in roh.values() if items]
open("widget.txt", "w", encoding="utf-8").write(
    "\n".join(f"• {z}" for z in zeilen) + f"\nStand {heute}"
)
