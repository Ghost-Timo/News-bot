import calendar, datetime, html, json, re, time, urllib.parse, urllib.request
import feedparser
from google import genai
from google.genai import types

MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]
STUNDEN = 48           # News: nur Meldungen der letzten X Stunden
STUNDEN_AKTIEN = 72    # Aktien-News
TOP_ANZAHL = 5         # Meldungen im Kasten "Das Wichtigste"
PRO_KATEGORIE = 3      # Meldungen pro Kategorie
PRO_AKTIE = 3          # Meldungen pro Aktie
MAX_KANDIDATEN = 20    # so viele Meldungen pro Gruppe sieht Gemini
FF_DATEI = "funfacts.json"

FEEDS = {
    "Welt": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Deutschland": "https://www.tagesschau.de/inland/index~rss2.xml",
    "Tech & KI": "https://www.heise.de/rss/heise-atom.xml",
    "Wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "Gaming & Roblox": "https://news.google.com/rss/search?q=Roblox&hl=de&gl=DE&ceid=DE:de",
}
STOCKS = {
    "Tesla": {"symbol": "TSLA", "suche": "Tesla Aktie"},
    "Nvidia": {"symbol": "NVDA", "suche": "Nvidia Aktie"},
    "Palantir": {"symbol": "PLTR", "suche": "Palantir Aktie"},
    "Arista (ANET)": {"symbol": "ANET", "suche": "Arista Networks Aktie"},
    "Bitcoin": {"symbol": "BTC-USD", "suche": "Bitcoin Kurs"},
}
heute = datetime.date.today().strftime("%d.%m.%Y")


def sauber(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt(x, nd=2):
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# 1) Meldungen holen (News und Aktien-News)
alle = []


def hole(kat, url, stunden):
    gesehen, liste = set(), []
    for e in feedparser.parse(url).entries:
        titel = sauber(e.get("title"))
        if not titel or titel in gesehen:
            continue
        if kat == "Gaming & Roblox" and re.search(r"\bcodes?\b", titel, re.I):
            continue
        p = e.get("published_parsed") or e.get("updated_parsed")
        if p and time.time() - calendar.timegm(p) > stunden * 3600:
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
    return liste


roh = {kat: hole(kat, url, STUNDEN) for kat, url in FEEDS.items()}
aktien_roh = {}
for name, cfg in STOCKS.items():
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(cfg["suche"])
           + "&hl=de&gl=DE&ceid=DE:de")
    aktien_roh[name] = hole(name, url, STUNDEN_AKTIEN)

by_id = {i["id"]: i for i in alle}


def holen(x):
    try:
        return by_id.get(int(x))
    except Exception:
        return None


# 2) Kurse holen und in Euro umrechnen
def usd_zu_eur():
    try:
        d = get_json("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?range=1d&interval=1d")
        return 1 / d["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except Exception as ex:
        print("Wechselkurs Yahoo:", ex)
    try:
        d = get_json("https://api.frankfurter.app/latest?from=USD&to=EUR")
        return d["rates"]["EUR"]
    except Exception as ex:
        print("Wechselkurs Frankfurter:", ex)
    return None


def kurs_yahoo(symbol):
    d = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d")
    m = d["chart"]["result"][0]["meta"]
    preis = m["regularMarketPrice"]
    vor = m.get("chartPreviousClose") or m.get("previousClose")
    return preis, ((preis - vor) / vor * 100 if vor else None)


def kurs_coingecko():
    d = get_json("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur&include_24hr_change=true")
    return d["bitcoin"]["eur"], d["bitcoin"].get("eur_24h_change"), "EUR"


kurs_eur = usd_zu_eur()
kurse = {}
for name, cfg in STOCKS.items():
    try:
        preis, chg = kurs_yahoo(cfg["symbol"])
        if kurs_eur:
            kurse[name] = (preis * kurs_eur, chg, "EUR")
        else:
            kurse[name] = (preis, chg, "USD")
    except Exception as ex:
        print("Kursfehler Yahoo:", name, ex)
        if name == "Bitcoin":
            try:
                kurse[name] = kurs_coingecko()
            except Exception as ex2:
                print("Kursfehler CoinGecko:", ex2)

# 3) Bisherige Fun Facts laden
try:
    with open(FF_DATEI, encoding="utf-8") as f:
        ff = json.load(f)
except Exception:
    ff = {}
bisher = ff.get("alle", []) if isinstance(ff, dict) else []
heute_facts = ff.get("facts_heute", []) if isinstance(ff, dict) and ff.get("datum") == heute else []
ff_hinweis = ""
if bisher:
    ff_hinweis = (" Diese Fun Facts wurden schon gezeigt, wiederhole keinen davon, auch nicht sinngemäß:\n"
                  + "\n".join("- " + x for x in bisher[-150:]) + "\n")

# 4) Eine Gemini-Anfrage für alles
prompt = (
    "Du bekommst nummerierte Meldungen (Titel und Kurztext aus RSS-Feeds) in zwei Teilen: NACHRICHTEN und AKTIEN. Aufgaben:\n"
    f"1) 'top': Wähle aus den NACHRICHTEN die {TOP_ANZAHL} wichtigsten des Tages über alle Kategorien. "
    "Pro Meldung: 'id', 'titel' (Deutsch, max. 8 Wörter) und 'text' (Deutsch, ca. 3 Sätze, die das Thema gut erklären). "
    "Nutze nur Informationen aus Titel und Kurztext, erfinde nichts dazu; gibt die Quelle wenig her, schreibe lieber kürzer.\n"
    f"2) 'auswahl': Nenne pro NACHRICHTEN-Kategorie die ids der bis zu {PRO_KATEGORIE} wichtigsten Meldungen, wichtigste zuerst.\n"
    f"3) 'aktien': Pro Aktie 'news' (ids der bis zu {PRO_AKTIE} für den Kurs relevantesten Meldungen) und 'einschaetzung' "
    "(Deutsch, 2-3 Sätze: Was könnten die Meldungen und die Tagesveränderung für den Kurs bedeuten? "
    "Vorsichtig formulieren, nur auf Basis der gegebenen Meldungen und Zahlen, keine eigenen Zahlen erfinden, "
    "keine Kaufempfehlung. Gibt es kaum relevante Meldungen, sag das offen.)\n"
    "4) 'funfacts': genau 3 kurze Fun Facts auf Deutsch (je 1-2 Sätze), bunt gemischt aus Wissenschaft, Geschichte, Natur, "
    f"Technik und Alltag. Heute ist der {heute}. Nur gut belegte Fakten; nenne Zahlen oder Jahre nur, wenn du dir sicher bist."
    + ff_hinweis +
    'Antworte nur mit JSON im Format {"top":[{"id":0,"titel":"","text":""}],"auswahl":{"Welt":[0]},'
    '"aktien":{"Tesla":{"news":[0],"einschaetzung":""}},"funfacts":["","",""]}\n\n'
    "=== NACHRICHTEN ===\n"
)
for kat, items in roh.items():
    prompt += f"[{kat}]\n"
    for i in items:
        prompt += f"{i['id']}: {i['titel']}" + (f" - {i['text']}" if i["text"] else "") + "\n"
    prompt += "\n"
prompt += "=== AKTIEN ===\n"
for name, items in aktien_roh.items():
    k = kurse.get(name)
    if k and k[0] is not None:
        info = f"Kurs {fmt(k[0])} {k[2]}" + (f", Tagesveränderung {fmt(k[1])} %" if k[1] is not None else "")
    else:
        info = "Kurs nicht verfügbar"
    prompt += f"[{name}] {info}\n"
    for i in items:
        prompt += f"{i['id']}: {i['titel']}" + (f" - {i['text']}" if i["text"] else "") + "\n"
    prompt += "\n"

client = genai.Client()
daten, fehler = None, []
for modell in MODELS:
    for versuch in range(3):
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
            time.sleep(20)
    if daten:
        break

top, auswahl, aktien_auswahl, einschaetzung, funfacts = [], {}, {}, {}, []
if daten:
    for t in (daten.get("top") or [])[:TOP_ANZAHL]:
        if not isinstance(t, dict):
            continue
        i = holen(t.get("id"))
        top.append({
            "titel": t.get("titel") or (i["titel"] if i else ""),
            "text": t.get("text", ""),
            "link": i["link"] if i else "",
        })
    for kat in roh:
        ids = (daten.get("auswahl") or {}).get(kat, [])
        gew = [holen(x) for x in ids]
        auswahl[kat] = [g for g in gew if g and g["kat"] == kat][:PRO_KATEGORIE]
    for name in STOCKS:
        a = (daten.get("aktien") or {}).get(name) or {}
        if not isinstance(a, dict):
            a = {}
        gew = [holen(x) for x in a.get("news", [])]
        aktien_auswahl[name] = [g for g in gew if g and g["kat"] == name][:PRO_AKTIE]
        einschaetzung[name] = a.get("einschaetzung", "")
    funfacts = [x for x in (daten.get("funfacts") or []) if isinstance(x, str)][:3]
for kat in roh:
    if not auswahl.get(kat):
        auswahl[kat] = roh[kat][:PRO_KATEGORIE]
for name in STOCKS:
    if not aktien_auswahl.get(name):
        aktien_auswahl[name] = aktien_roh[name][:PRO_AKTIE]

# Fun Facts: am selben Tag bleiben sie gleich, neue werden gemerkt
if heute_facts:
    funfacts = heute_facts
elif funfacts:
    bisher = (bisher + funfacts)[-300:]
    heute_facts = funfacts
with open(FF_DATEI, "w", encoding="utf-8") as f:
    json.dump({"datum": heute, "facts_heute": heute_facts, "alle": bisher},
              f, ensure_ascii=False, indent=1)


# 5) Seite bauen
def verlinkt(titel, link):
    t = html.escape(titel)
    if link:
        t = f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">{t}</a>'
    return t


def news_li(i):
    text = f'<div class="t">{html.escape(i["text"])}</div>' if i["text"] else ""
    return f'<li>{verlinkt(i["titel"], i["link"])}{text}</li>'


def btn(group, target, label, on=False):
    return (f'<button data-group="{group}" data-target="{target}"'
            f'{" class=on" if on else ""}>{html.escape(label)}</button>')


if top:
    oben = "<ul>" + "".join(
        f'<li><b>{verlinkt(t["titel"], t["link"])}</b><div class="s">{html.escape(t["text"])}</div></li>'
        for t in top
    ) + "</ul>"
else:
    oben = ("<p><i>Zusammenfassung nicht verfügbar.</i></p><p class='err'>"
            + html.escape(" | ".join(fehler)) + "</p>")

unten = ""
for kat, items in auswahl.items():
    liste = "".join(news_li(i) for i in items) or "<li>Keine neuen Meldungen.</li>"
    unten += f"<h3>{html.escape(kat)}</h3><ul>{liste}</ul>"


def aktie_panel(idx, name):
    k = kurse.get(name)
    kurs = "Kurs nicht verfügbar"
    if k and k[0] is not None:
        zeichen = "€" if k[2] == "EUR" else "USD"
        kurs = f"{fmt(k[0])} {zeichen}"
        if k[1] is not None:
            klasse = "up" if k[1] >= 0 else "down"
            pfeil = "▲" if k[1] >= 0 else "▼"
            kurs += f' <span class="{klasse}">{pfeil} {fmt(abs(k[1]))} %</span>'
    text = einschaetzung.get(name) or "Keine Einschätzung verfügbar."
    news = "".join(news_li(i) for i in aktien_auswahl.get(name, [])) or "<li>Keine aktuellen Meldungen.</li>"
    versteckt = "" if idx == 0 else " hidden"
    return (
        f'<div data-panel="aktien" id="a{idx}"{versteckt}>'
        f'<div class="kurs"><b>{html.escape(name)}</b><div class="preis">{kurs}</div></div>'
        f'<h3>Einschätzung <small>(KI-Vermutung, keine Anlageberatung)</small></h3>'
        f'<p>{html.escape(text)}</p><h3>Nachrichten</h3><ul>{news}</ul></div>'
    )


sub_btns = "".join(btn("aktien", f"a{n}", name, n == 0) for n, name in enumerate(STOCKS))
aktien_html = "".join(aktie_panel(n, name) for n, name in enumerate(STOCKS))

if funfacts:
    fun_html = "<ol>" + "".join(f"<li>{html.escape(x)}</li>" for x in funfacts) + "</ol>"
else:
    fun_html = "<p>Fun Facts gerade nicht verfügbar.</p>"

CSS = """
body{font-family:sans-serif;background:#111;color:#eee;margin:0;line-height:1.45}
.wrap{max-width:900px;margin:auto;padding:16px}
nav{position:sticky;top:0;background:#111;padding:10px 16px;border-bottom:1px solid #333;overflow-x:auto;white-space:nowrap;z-index:5}
.sub{overflow-x:auto;white-space:nowrap;margin:8px 0 18px}
button{background:#222;color:#ddd;border:1px solid #444;border-radius:20px;padding:8px 16px;margin-right:8px;font-size:1em;cursor:pointer}
button.on{background:#7cc4ff;color:#111;border-color:#7cc4ff;font-weight:bold}
[hidden]{display:none!important}
h2{color:#7cc4ff;margin:0 0 6px} h3{color:#7cc4ff;margin:22px 0 4px} h3 small{color:#888;font-weight:normal;font-size:.7em}
.top{background:#1c2733;border-radius:12px;padding:14px 16px;margin:12px 0 24px}
.top li{margin:14px 0;font-size:1.05em} .s{color:#ccc;font-size:.92em;margin-top:3px}
li{margin:8px 0} a{color:#eee;text-decoration:none} a:hover{text-decoration:underline}
.t{color:#999;font-size:.85em;margin-top:2px} .err{color:#e88;font-size:.8em}
.kurs{background:#1c2733;border-radius:12px;padding:14px 16px;font-size:1.1em}
.preis{font-size:1.5em;margin-top:4px} .up{color:#6fd16f} .down{color:#f07070}
.foot{color:#777;font-size:.8em;margin-top:32px}
"""

JS = """
document.querySelectorAll('button[data-group]').forEach(function(b){
  b.addEventListener('click', function(){
    var g = b.dataset.group, t = b.dataset.target;
    document.querySelectorAll('button[data-group="'+g+'"]').forEach(function(x){x.classList.toggle('on', x===b);});
    document.querySelectorAll('[data-panel="'+g+'"]').forEach(function(p){p.hidden = (p.id !== t);});
  });
});
"""

page = (
    '<!doctype html><html lang="de"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>News</title><style>" + CSS + "</style>"
    "<nav>" + btn("main", "p-news", "News", True) + btn("main", "p-aktien", "Aktien")
    + btn("main", "p-fun", "Fun Facts") + "</nav>"
    f'<div class="wrap"><h1>News {heute}</h1>'
    f'<section data-panel="main" id="p-news"><div class="top"><h2>Das Wichtigste</h2>{oben}</div>'
    f"<h2>Weitere Meldungen</h2>{unten}</section>"
    f'<section data-panel="main" id="p-aktien" hidden><div class="sub">{sub_btns}</div>{aktien_html}</section>'
    f'<section data-panel="main" id="p-fun" hidden><h2>Fun Facts</h2>{fun_html}</section>'
    '<p class="foot">Zusammenfassungen, Einschätzungen und Fun Facts sind von einer KI geschrieben und können Fehler '
    f"enthalten. Keine Anlageberatung. Stand: {heute}</p></div>"
    "<script>" + JS + "</script>"
)
open("index.html", "w", encoding="utf-8").write(page)

# 6) Kurze Textdatei (bleibt, damit der Workflow-Schritt weiter funktioniert)
if top:
    zeilen = [t["titel"] for t in top]
else:
    zeilen = [items[0]["titel"] for items in roh.values() if items]
open("widget.txt", "w", encoding="utf-8").write(
    "\n".join(f"• {z}" for z in zeilen) + f"\nStand {heute}"
)
