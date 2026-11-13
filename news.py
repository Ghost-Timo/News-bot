import calendar, datetime, html, json, re, time, urllib.parse, urllib.request
import feedparser
from google import genai
from google.genai import types
from zoneinfo import ZoneInfo

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
jetzt = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
heute = jetzt.strftime("%d.%m.%Y")
stand = jetzt.strftime("%d.%m.%Y, %H:%M Uhr")


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
        roh_text = sauber(e.get("summary"))
        text = roh_text
        if len(text) < 20 or titel[:30] in text:
            text = ""
        kontext = re.sub(r"\s+", " ", roh_text.replace(titel, " ")).strip(" -–|")
        if len(kontext) < 20:
            kontext = ""
        gesehen.add(titel)
        item = {"id": len(alle), "kat": kat, "titel": titel,
                "text": text[:300], "kontext": kontext[:400], "link": e.get("link", "")}
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


# 2) Kurse und Verläufe holen, in Euro umrechnen
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


def hist_yahoo(symbol):
    d = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d")
    c = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [x for x in c if x is not None]


def hist_coingecko():
    d = get_json("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=eur&days=30&interval=daily")
    return [p[1] for p in d["prices"]]


kurs_eur = usd_zu_eur()
kurse, verlaeufe = {}, {}
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
    try:
        werte = hist_yahoo(cfg["symbol"])
        if kurs_eur:
            verlaeufe[name] = ([w * kurs_eur for w in werte], "EUR")
        else:
            verlaeufe[name] = (werte, "USD")
    except Exception as ex:
        print("Verlaufsfehler Yahoo:", name, ex)
        if name == "Bitcoin":
            try:
                verlaeufe[name] = (hist_coingecko(), "EUR")
            except Exception as ex2:
                print("Verlaufsfehler CoinGecko:", ex2)

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
    "Du bekommst nummerierte Meldungen (Titel und Kontext aus RSS-Feeds) in zwei Teilen: NACHRICHTEN und AKTIEN. Aufgaben:\n"
    f"1) 'top': Wähle aus den NACHRICHTEN die {TOP_ANZAHL} wichtigsten des Tages über alle Kategorien. "
    "Pro Meldung: 'id', 'titel' (Deutsch, max. 8 Wörter) und 'text' (Deutsch, ca. 3 Sätze, die das Thema gut erklären). "
    "Nutze nur Informationen aus Titel und Kontext, erfinde nichts dazu; gibt die Quelle wenig her, schreibe lieber kürzer.\n"
    f"2) 'auswahl': Nenne pro NACHRICHTEN-Kategorie die ids der bis zu {PRO_KATEGORIE} wichtigsten Meldungen, wichtigste zuerst.\n"
    f"3) 'aktien': Pro Aktie 'news' (ids der bis zu {PRO_AKTIE} für den Kurs relevantesten Meldungen) und 'einschaetzung' "
    "(Deutsch, 2-3 Sätze: Was könnten die Meldungen und die Tagesveränderung für den Kurs bedeuten? "
    "Vorsichtig formulieren, nur auf Basis der gegebenen Meldungen und Zahlen, keine eigenen Zahlen erfinden, "
    "keine Kaufempfehlung. Gibt es kaum relevante Meldungen, sag das offen.)\n"
    "4) 'funfacts': genau 3 kurze Fun Facts auf Deutsch (je 1-2 Sätze), bunt gemischt aus Wissenschaft, Geschichte, Natur, "
    f"Technik und Alltag. Heute ist der {heute}. Nur gut belegte Fakten; nenne Zahlen oder Jahre nur, wenn du dir sicher bist."
    + ff_hinweis +
    "5) 'erklaerungen': Ein Objekt, das jede id, die du in 'auswahl' oder in 'aktien' unter 'news' genannt hast, auf eine "
    "Erklärung abbildet (nicht für 'top'). Die Erklärung hat 1-2 Sätze auf Deutsch und sagt, was passiert ist und was die "
    "Schlagzeile meint, sodass sie auch ohne den Artikel verständlich ist. Nutze den Titel, den Kontext dieser Meldung und die "
    "anderen Meldungen derselben Gruppe. Erfinde nichts dazu: Reicht das Material nicht, schreibe nur, was sich sicher daraus "
    "ergibt.\n"
    'Antworte nur mit JSON im Format {"top":[{"id":0,"titel":"","text":""}],"auswahl":{"Welt":[0]},'
    '"aktien":{"Tesla":{"news":[0],"einschaetzung":""}},"funfacts":["","",""],"erklaerungen":{"0":""}}\n\n'
    "=== NACHRICHTEN ===\n"
)
for kat, items in roh.items():
    prompt += f"[{kat}]\n"
    for i in items:
        prompt += f"{i['id']}: {i['titel']}" + (f" - Kontext: {i['kontext']}" if i["kontext"] else "") + "\n"
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
        prompt += f"{i['id']}: {i['titel']}" + (f" - Kontext: {i['kontext']}" if i["kontext"] else "") + "\n"
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

top, auswahl, aktien_auswahl, einschaetzung, funfacts, erklaerungen = [], {}, {}, {}, [], {}
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
    erk = daten.get("erklaerungen") or {}
    if isinstance(erk, dict):
        erklaerungen = {str(k): v for k, v in erk.items() if isinstance(v, str)}
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
    erkl = erklaerungen.get(str(i["id"]))
    if erkl:
        extra = f'<div class="e">{html.escape(erkl)}</div>'
    elif i["text"]:
        extra = f'<div class="t">{html.escape(i["text"])}</div>'
    else:
        extra = ""
    return f'<li>{verlinkt(i["titel"], i["link"])}{extra}</li>'


def btn(group, target, label, on=False):
    return (f'<button data-group="{group}" data-target="{target}"'
            f'{" class=on" if on else ""}>{html.escape(label)}</button>')


def pfad(werte, w, h, pad):
    mn, mx = min(werte), max(werte)
    r = (mx - mn) or 1
    n = (len(werte) - 1) or 1
    teile = []
    for i, v in enumerate(werte):
        x = pad + i * (w - 2 * pad) / n
        y = h - pad - (v - mn) / r * (h - 2 * pad)
        teile.append(("L" if i else "M") + f"{x:.1f} {y:.1f}")
    return " ".join(teile)


def diagramme(name):
    v = verlaeufe.get(name)
    if not v or len(v[0]) < 2:
        return "", '<p class="t">Verlauf nicht verfügbar.</p>'
    werte, waehrung = v
    zeichen = "€" if waehrung == "EUR" else "USD"
    klein = (f'<svg viewBox="0 0 90 28" width="90" height="28" aria-hidden="true">'
             f'<path d="{pfad(werte, 90, 28, 2)}" fill="none" stroke="#7cc4ff" stroke-width="2" '
             f'stroke-linejoin="round" stroke-linecap="round"/></svg>')
    p = pfad(werte, 640, 150, 10)
    gross = (f'<svg viewBox="0 0 640 150" width="100%" role="img" aria-label="Kursverlauf des letzten Monats">'
             f'<path d="{p} L630 140 L10 140 Z" fill="#7cc4ff" opacity="0.15"/>'
             f'<path d="{p}" fill="none" stroke="#7cc4ff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>'
             f'<div class="axis"><span>vor einem Monat</span>'
             f'<span>Tief {fmt(min(werte))} {zeichen} · Hoch {fmt(max(werte))} {zeichen}</span><span>heute</span></div>')
    return klein, gross


def kurs_text(name):
    k = kurse.get(name)
    if not (k and k[0] is not None):
        return "Kurs nicht verfügbar", ""
    zeichen = "€" if k[2] == "EUR" else "USD"
    preis = f"{fmt(k[0])} {zeichen}"
    chg = ""
    if k[1] is not None:
        klasse = "up" if k[1] >= 0 else "down"
        pfeil = "▲" if k[1] >= 0 else "▼"
        chg = f'<span class="{klasse}">{pfeil} {fmt(abs(k[1]))} %</span>'
    return preis, chg


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


def uebersicht_panel():
    zeilen, grosse = "", ""
    for n, name in enumerate(STOCKS):
        preis, chg = kurs_text(name)
        klein, gross = diagramme(name)
        aktiv = " on" if n == 0 else ""
        versteckt = "" if n == 0 else " hidden"
        zeilen += (f'<div class="row{aktiv}" data-i="{n}"><span class="nm">{html.escape(name)}</span>'
                   f'<span>{preis}</span><span>{chg}</span>{klein}</div>')
        grosse += (f'<div data-big="{n}"{versteckt}><p class="lab">{html.escape(name)}: Verlauf des letzten Monats</p>'
                   f'{gross}</div>')
    return (f'<div data-panel="aktien" id="a-ue"><div class="box">{zeilen}</div>'
            f'<div class="box">{grosse}</div></div>')


def aktie_panel(idx, name):
    preis, chg = kurs_text(name)
    klein, gross = diagramme(name)
    text = einschaetzung.get(name) or "Keine Einschätzung verfügbar."
    news = "".join(news_li(i) for i in aktien_auswahl.get(name, [])) or "<li>Keine aktuellen Meldungen.</li>"
    return (
        f'<div data-panel="aktien" id="a{idx}" hidden>'
        f'<div class="kurs"><b>{html.escape(name)}</b><div class="preis">{preis} {chg}</div>'
        f'<div class="stand">Abgerufen: {stand}</div></div>'
        f'<div class="box">{gross}</div>'
        f'<h3>Einschätzung <small>(KI-Vermutung, keine Anlageberatung)</small></h3>'
        f'<p>{html.escape(text)}</p><h3>Nachrichten</h3><ul>{news}</ul></div>'
    )


sub_btns = btn("aktien", "a-ue", "Übersicht", True) + "".join(
    btn("aktien", f"a{n}", name) for n, name in enumerate(STOCKS))
aktien_html = uebersicht_panel() + "".join(aktie_panel(n, name) for n, name in enumerate(STOCKS))

if funfacts:
    fun_html = "<ol>" + "".join(f"<li>{html.escape(x)}</li>" for x in funfacts) + "</ol>"
else:
    fun_html = "<p>Fun Facts gerade nicht verfügbar.</p>"

todo_html = (
    '<h2>To-do</h2><div class="trow"><input type="text" id="tinp" placeholder="Neue Aufgabe">'
    '<button id="tadd">Hinzufügen</button></div><p class="err" id="terr"></p><div id="tlist"></div>'
    '<p class="foot">Die Liste wird nur in diesem Browser gespeichert. Auf jedem Gerät ist sie getrennt.</p>'
)

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
li{margin:10px 0} a{color:#eee;text-decoration:none} a:hover{text-decoration:underline}
.t{color:#999;font-size:.85em;margin-top:2px} .err{color:#e88;font-size:.8em}
.e{color:#c8c8c8;font-size:.9em;margin-top:3px}
.kurs{background:#1c2733;border-radius:12px;padding:14px 16px;font-size:1.1em;margin-bottom:12px}
.preis{font-size:1.5em;margin-top:4px} .up{color:#6fd16f} .down{color:#f07070}
.foot{color:#777;font-size:.8em;margin-top:32px}
.stand{color:#888;font-size:.85em;margin:4px 0 12px}
.box{background:#1a1a19;border-radius:12px;padding:12px 16px;margin:0 0 14px}
.lab{color:#aaa;font-size:.9em;margin:0 0 4px}
.row{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) minmax(0,.8fr) 90px;gap:12px;align-items:center;padding:10px 8px;border-bottom:1px solid #2a2a2a;cursor:pointer;border-radius:8px}
.row:last-child{border-bottom:none} .row.on{background:#1c2733} .nm{font-weight:bold}
.axis{display:flex;justify-content:space-between;gap:8px;font-size:.75em;color:#888;flex-wrap:wrap}
.trow{display:flex;gap:8px;margin-top:8px}
input[type=text]{flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:8px;padding:10px;font-size:1em}
.todo{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid #2a2a2a}
.todo span{flex:1} .todo.done span{text-decoration:line-through;color:#777}
.todo input{width:20px;height:20px}
.ib{background:none;border:none;margin:0;padding:4px 10px;color:#888;font-size:1.3em}
"""

JS = """
document.querySelectorAll('button[data-group]').forEach(function(b){
  b.addEventListener('click', function(){
    var g = b.dataset.group, t = b.dataset.target;
    document.querySelectorAll('button[data-group="'+g+'"]').forEach(function(x){x.classList.toggle('on', x===b);});
    document.querySelectorAll('[data-panel="'+g+'"]').forEach(function(p){p.hidden = (p.id !== t);});
  });
});
document.querySelectorAll('.row[data-i]').forEach(function(r){
  r.addEventListener('click', function(){
    document.querySelectorAll('.row[data-i]').forEach(function(x){x.classList.toggle('on', x===r);});
    document.querySelectorAll('[data-big]').forEach(function(b){b.hidden = (b.dataset.big !== r.dataset.i);});
  });
});
(function(){
  var KEY='newsbot_todos', todos=[];
  try{todos=JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){todos=[];}
  if(!Array.isArray(todos)){todos=[];}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(todos));}catch(e){}}
  var list=document.getElementById('tlist'), inp=document.getElementById('tinp'), err=document.getElementById('terr');
  function render(){
    list.innerHTML='';
    todos.forEach(function(x,i){
      var row=document.createElement('div'); row.className='todo'+(x.d?' done':'');
      var cb=document.createElement('input'); cb.type='checkbox'; cb.checked=!!x.d;
      cb.addEventListener('change',function(){x.d=cb.checked; save(); render();});
      var sp=document.createElement('span'); sp.textContent=x.t;
      var del=document.createElement('button'); del.className='ib'; del.textContent='×';
      del.setAttribute('aria-label','Aufgabe löschen');
      del.addEventListener('click',function(){todos.splice(i,1); save(); render();});
      row.appendChild(cb); row.appendChild(sp); row.appendChild(del); list.appendChild(row);
    });
    if(!todos.length){list.innerHTML='<p class="t">Noch keine Aufgaben.</p>';}
  }
  function add(){
    var v=inp.value.trim();
    if(!v){err.textContent='Schreib zuerst eine Aufgabe.'; return;}
    todos.unshift({t:v,d:false}); inp.value=''; err.textContent=''; save(); render();
  }
  document.getElementById('tadd').addEventListener('click',add);
  inp.addEventListener('keydown',function(e){if(e.key==='Enter'){add();}});
  inp.addEventListener('input',function(){err.textContent='';});
  render();
})();
"""

page = (
    '<!doctype html><html lang="de"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>News</title><style>" + CSS + "</style>"
    "<nav>" + btn("main", "p-news", "News", True) + btn("main", "p-aktien", "Aktien")
    + btn("main", "p-fun", "Fun Facts") + btn("main", "p-todo", "To-do") + "</nav>"
    f'<div class="wrap"><h1>News {heute}</h1><div class="stand">Stand: {stand}</div>'
    f'<section data-panel="main" id="p-news"><div class="top"><h2>Das Wichtigste</h2>{oben}</div>'
    f"<h2>Weitere Meldungen</h2>{unten}</section>"
    f'<section data-panel="main" id="p-aktien" hidden><div class="sub">{sub_btns}</div>{aktien_html}</section>'
    f'<section data-panel="main" id="p-fun" hidden><h2>Fun Facts</h2>{fun_html}</section>'
    f'<section data-panel="main" id="p-todo" hidden>{todo_html}</section>'
    '<p class="foot">Zusammenfassungen, Erklärungen, Einschätzungen und Fun Facts sind von einer KI geschrieben und '
    f"können Fehler enthalten. Keine Anlageberatung. Stand: {stand}</p></div>"
    "<script>" + JS + "</script>"
)
open("index.html", "w", encoding="utf-8").write(page)

# 6) Kurze Textdatei (bleibt, damit der Workflow-Schritt weiter funktioniert)
if top:
    zeilen = [t["titel"] for t in top]
else:
    zeilen = [items[0]["titel"] for items in roh.values() if items]
open("widget.txt", "w", encoding="utf-8").write(
    "\n".join(f"• {z}" for z in zeilen) + f"\nStand {stand}"
)
