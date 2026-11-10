#!/usr/bin/env python3
"""
Prague Cultural Events Scraper
================================
Scrapes multiple sources for upcoming Prague cultural events and generates
a self-contained index.html with a calendar date-picker and language switcher
(Czech / English / Russian / Ukrainian / Vietnamese).

Run manually:  python scraper.py
Automation:    run setup.bat once → Windows Task Scheduler runs it daily at 07:00

FACEBOOK NOTE: Facebook shut down public event data via API in 2018.
Reliable programmatic access requires a user access token — not suitable for
automated scripts.

ONLINE DEPLOYMENT: index.html is fully static.
1. Push this folder to GitHub + enable GitHub Pages.
2. Add .github/workflows/update.yml — a scheduled Action runs scraper.py
   daily and commits the result. Free with GitHub Actions.
"""

import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run:  pip install requests beautifulsoup4 lxml")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_FILE   = SCRIPT_DIR / "index.html"
LOG_FILE   = SCRIPT_DIR / "scraper.log"
STATE_FILE = SCRIPT_DIR / "seen_events.json"   # tracks when each event first appeared

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

_sess = requests.Session()
_sess.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


# ─── HTTP helper ──────────────────────────────────────────────────────────────

def get(url: str, params=None, delay: float = 1.5, extra_headers=None):
    time.sleep(delay)
    try:
        hdrs = dict(extra_headers) if extra_headers else {}
        r = _sess.get(url, params=params, headers=hdrs, timeout=20, allow_redirects=True)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        log.warning("GET %s -> %s", url, e)
        return None


# ─── Category helpers ─────────────────────────────────────────────────────────

_KEYWORDS = {
    "cinema":     ["kino", "film", "cinema", "movie", "projekce", "screening",
                   "premiéra", "premiere", "dokumentár", "animovaný", "short film"],
    "theater":    ["divadlo", "theater", "theatre", "opera", "balet", "ballet",
                   "muzikál", "musical", "kabaret", "pantomima", "komedie", "drama",
                   "tragédie", "fringe", "stand-up", "standup", "comedy", "humor",
                   "improvizace", "improv", "kabaretní", "satirické", "sketch"],
    "concert":    ["koncert", "concert", "jazz", "rock", "pop", "klasická",
                   "filharmonie", "hudba", "music", "recitál", "folk", "metal",
                   "choir", "zpěv", "symphony", "orchestra", "band", "gig", "live music"],
    "festival":   ["festival", " fest ", " fest,", " fest\n", "přehlídka",
                   "open-air festival", "filmový festival", "hudební festival"],
    "exhibition": ["výstava", "exhibition", "galerie", "gallery", "vernisáž",
                   "muzeum", "museum", "výstaviště", "umění", "art show", "expo",
                   "installation", "photography", "sculpture"],
    "outdoor":    ["open air", "openair", "open-air", "outdoor", "venkovní",
                   "piknik", "street food", "farmářský", "jarmark", "trh ",
                   "park ", "náměstí", "market", "rooftop", "garden party"],
    "dance":      ["tanec", "dance", "taneční", "dj set", "techno", "club night",
                   "disco", "rave", "tango", "salsa", "swing"],
    "family":     ["pro děti", "dětský", "rodinný", "pohádka", "children",
                   "kids", "family", "marionety", "loutk", "puppet"],
    "sports":     ["sport", "maraton", "marathon", "běh", "run", "tournament",
                   "match", "game", "league", "fitness", "yoga", "workout"],
}

_CAT_LABELS = {
    "cinema":     "Cinema",
    "theater":    "Theater",
    "standup":    "Stand-up",
    "concert":    "Music",
    "festival":   "Festival",
    "exhibition": "Exhibition",
    "outdoor":    "Open Air",
    "dance":      "Dance",
    "family":     "Family",
    "sports":     "Sports",
    "other":      "Other",
}

_CAT_COLORS = {
    "cinema":     "#e74c3c",
    "theater":    "#8e44ad",
    "standup":    "#f39c12",
    "concert":    "#2980b9",
    "festival":   "#e67e22",
    "exhibition": "#27ae60",
    "outdoor":    "#1abc9c",
    "dance":      "#e91e63",
    "family":     "#3498db",
    "sports":     "#16a085",
    "other":      "#7f8c8d",
}

_CAT_ICONS = {
    "cinema":     "🎬",
    "theater":    "🎭",
    "standup":    "🎤",
    "concert":    "🎵",
    "festival":   "🎪",
    "exhibition": "🎨",
    "outdoor":    "🌿",
    "dance":      "💃",
    "family":     "👨‍👩‍👧",
    "sports":     "🏃",
    "other":      "⭐",
}

# Stand-up is detected by title/description since GoOut's tag is sparse
_STANDUP_RE = re.compile(r"(stand[-\s]?up|na\s+stojáka|na\s+stojaka|open\s*mic)", re.I)


def classify(text: str) -> str:
    t = text.lower()
    for cat, kws in _KEYWORDS.items():
        if any(kw in t for kw in kws):
            return cat
    return "other"


def extract_date(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
    if m:
        d, mo, y = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else ""


_ISO_DATE  = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHS_EN = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def _parse_dt(raw) -> tuple:
    """Parse any date/datetime string → (YYYY-MM-DD, HH:MM). Returns ('','') on failure."""
    if not raw:
        return "", ""
    s = str(raw).strip()
    # ISO 8601:  YYYY-MM-DD[T ]HH:MM
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        date = s[:10]
        t = re.search(r"[T ](\d{2}:\d{2})", s)
        return date, (t.group(1) if t else "")
    # JS Date.toString():  "Wed Nov 04 2026 12:30:00 GMT+0000 (...)"  → weekday MONTH day year
    m = re.search(r"\b([A-Za-z]{3})\w*\s+(\d{1,2})\s+(\d{4})", s)
    if m:
        mon, d, y = m.groups()
        mo = _MONTHS_EN.get(mon[:3].lower(), 0)
        if mo:
            try:
                date = datetime(int(y), mo, int(d)).strftime("%Y-%m-%d")
                t = re.search(r"\b(\d{2}:\d{2})\b", s)
                return date, (t.group(1) if t else "")
            except ValueError:
                pass
    # RFC 2822:  "Wed, 05 Nov 2026 20:00:00"  → day MONTH year
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", s)
    if m:
        d, mon, y = m.groups()
        mo = _MONTHS_EN.get(mon[:3].lower(), 0)
        if mo:
            try:
                date = datetime(int(y), mo, int(d)).strftime("%Y-%m-%d")
                t = re.search(r"\b(\d{2}:\d{2})\b", s)
                return date, (t.group(1) if t else "")
            except ValueError:
                pass
    # Czech:  DD.MM.YYYY [HH:MM]
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s)
    if m:
        d, mo, y = m.groups()
        try:
            date = datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
            t = re.search(r"\b(\d{1,2}:\d{2})\b", s)
            return date, (t.group(1) if t else "")
        except ValueError:
            pass
    return "", ""


def _ev(title, date="", time_="", venue="Praha", category=None,
        description="", url="", image="", source="", end="",
        subcat="", recommended=False, popularity=0):
    # Normalise: if date isn't already exactly YYYY-MM-DD, parse the full string
    if date and not _ISO_DATE.match(str(date)):
        d, t = _parse_dt(date)
        date = d
        if not time_:
            time_ = t
    if end and not _ISO_DATE.match(str(end)):
        end, _ = _parse_dt(end)
    desc = (description or "").strip()
    cat = category or classify(title + " " + desc)
    # Stand-up is its own category — overrides whatever the source assigned
    if _STANDUP_RE.search(title + " " + desc):
        cat = "standup"
        subcat = ""
    return {
        "title":       title.strip(),
        "date":        date or "",
        "end":         end or "",
        "time":        time_[:5] if time_ else "",
        "venue":       venue.strip() or "Praha",
        "category":    cat,
        "subcat":      subcat or "",
        "recommended": bool(recommended),
        "popularity":  int(popularity or 0),
        "description": desc[:200],
        "url":         url.strip(),
        "image":       image.strip(),
        "source":      source,
    }


# ─── Source 1: GoOut.net (public entities API) ───────────────────────────────

# GoOut's own site renders listings client-side; only ~9 events appear in static
# HTML. Its public JSON API returns the complete, paginated schedule instead.
_GOOUT_API   = "https://goout.net/services/entities/v1/schedules/"
_GOOUT_PRAHA = 101748113          # Prague city id (from venue→city relationship)

# GoOut mainCategory → our category
_GOOUT_CAT = {
    "concerts":    "concert",
    "exhibitions": "exhibition",
    "play":        "theater",
    "theatre":     "theater",
    "film":        "cinema",
    "for_children":"family",
    "festivals":   "festival",
    "clubbing":    "dance",
    "dancing":     "dance",
    "sport":       "sports",
    "sports":      "sports",
    "gastronomy":  "outdoor",
    "in_city":     "other",
}

# GoOut tag → (our category, subcategory key). Drives the two-tier filter.
_GOOUT_SUBCAT = {
    # music
    "rock": ("concert","rock"), "pop": ("concert","pop"), "classical": ("concert","classical"),
    "jazz": ("concert","jazz"), "electronic": ("concert","electronic"), "folk": ("concert","folk"),
    "punk": ("concert","punk"), "metal": ("concert","metal"), "rap": ("concert","rap"),
    "hiphop": ("concert","rap"), "concert_alternative": ("concert","alternative"),
    "blues": ("concert","blues"), "world_music": ("concert","world"),
    # theater
    "comedy": ("theater","comedy"), "drama": ("theater","drama"),
    "play_puppets": ("theater","puppets"), "play_performance": ("theater","performance"),
    "musical": ("theater","musical"), "opera": ("theater","opera"), "ballet": ("theater","ballet"),
    # film
    "film_thriller": ("cinema","thriller"), "film_animated": ("cinema","animated"),
    "film_action": ("cinema","action"), "film_romantic": ("cinema","romantic"),
    "film_historic": ("cinema","historic"), "film_adventure": ("cinema","adventure"),
    "film_family": ("cinema","family"), "film_documentary": ("cinema","documentary"),
    "film_comedy": ("cinema","comedy"), "film_horror": ("cinema","horror"),
    # exhibitions
    "exhibitions_history": ("exhibition","history"),
    "exhibitions_contemporary": ("exhibition","contemporary"),
    "exhibitions_paintings": ("exhibition","paintings"),
    "exhibitions_photography": ("exhibition","photography"),
    # family
    "for_children_educational": ("family","educational"),
    "fairy_tale": ("family","fairytale"), "under_age_3": ("family","toddlers"),
}

def _pick_subcat(tags, category):
    """Return the first tag that maps to a subcategory of this event's category."""
    for t in tags or []:
        hit = _GOOUT_SUBCAT.get(t)
        if hit and hit[0] == category:
            return hit[1]
    return ""

def scrape_goout(max_pages: int = 110) -> list:
    by_event = {}          # event_id → aggregated event dict (one card per event)
    scroll = None

    for _ in range(max_pages):
        params = {"languages[]": "cs", "include": "events,venues,images,sales"}
        if scroll:
            params["scrollId"] = scroll
        # Retry transient failures/throttling instead of truncating the whole crawl
        j = None
        for attempt in range(4):
            r = get(_GOOUT_API, params=params, delay=0.5 + 0.8 * attempt,
                    extra_headers={"Accept": "application/json"})
            if r:
                try:
                    j = r.json()
                    break
                except Exception:
                    j = None
        if j is None:
            log.warning("GoOut: giving up after retries at page with scroll=%s", bool(scroll))
            break

        scheds = j.get("schedules", [])
        if not scheds:
            break
        inc = j.get("included", {})
        events  = {e["id"]: e for e in inc.get("events", [])}
        venues  = {v["id"]: v for v in inc.get("venues", [])}
        images  = {i["id"]: i for i in inc.get("images", [])}
        sales   = {s["id"]: s for s in inc.get("sales", [])}

        for s in scheds:
            rel = s.get("relationships", {})
            vid = (rel.get("venue") or {}).get("id")
            venue = venues.get(vid)
            if not venue:
                continue
            # Keep Prague only
            if (venue.get("relationships", {}).get("city", {}) or {}).get("id") != _GOOUT_PRAHA:
                continue
            eid = (rel.get("event") or {}).get("id")
            ev = events.get(eid)
            if not ev:
                continue

            start = s.get("attributes", {}).get("startAt", "")
            if not start:
                continue
            loc = ev.get("locales", {})
            name = ((loc.get("cs") or loc.get("en") or {}).get("name") or "").strip()
            if not name:
                continue

            rec = by_event.get(eid)
            if rec is None:
                vloc = venue.get("locales", {})
                vname = ((vloc.get("cs") or vloc.get("en") or {}).get("name") or "Praha").strip()
                desc = re.sub(r"<[^>]+>", "",
                              (loc.get("cs") or loc.get("en") or {}).get("description", ""))[:200]
                # image
                img = ""
                for im in ev.get("relationships", {}).get("images", []) or []:
                    io = images.get(im.get("id"))
                    if io and io.get("attributes", {}).get("url"):
                        img = io["attributes"]["url"]; break
                attrs = ev.get("attributes", {})
                maincat = attrs.get("mainCategory", "")
                cat = _GOOUT_CAT.get(maincat) or classify(name + " " + desc)
                subcat = _pick_subcat(attrs.get("tags", []), cat)
                recommended = attrs.get("recommendation") == "recommended"
                followers = (venue.get("attributes", {}).get("counts", {}) or {}).get("followers", 0) or 0
                # Real event/ticket URL comes from the sale; /event/<id> dead-ends
                sid = (rel.get("sale") or {}).get("id")
                ev_url = (sales.get(sid, {}).get("attributes", {}) or {}).get("saleUrl", "") if sid else ""
                rec = {"name": name, "venue": vname, "desc": desc, "img": img,
                       "cat": cat, "subcat": subcat, "recommended": recommended,
                       "followers": followers, "url": ev_url,
                       "start": start, "end": start}
                by_event[eid] = rec
            else:
                if start < rec["start"]:
                    rec["start"] = start
                if start > rec["end"]:
                    rec["end"] = start
                # Backfill the ticket URL if the first schedule had no sale
                if not rec["url"]:
                    sid = (rel.get("sale") or {}).get("id")
                    if sid:
                        rec["url"] = (sales.get(sid, {}).get("attributes", {}) or {}).get("saleUrl", "")

        scroll = j.get("meta", {}).get("nextScrollId")
        if not scroll:
            break

    out = [_ev(title=r["name"], date=r["start"], end=r["end"], time_="",
               venue=r["venue"], category=r["cat"], description=r["desc"],
               url=r["url"], image=r["img"], source="GoOut.net",
               subcat=r["subcat"], recommended=r["recommended"], popularity=r["followers"])
           for r in by_event.values()]
    log.info("GoOut: %d events (%d recommended)",
             len(out), sum(1 for e in out if e["recommended"]))
    return out


# ─── Source 2: KudyZNudy.cz (national Czech events calendar, Prague filter) ───

def scrape_kudyznudy() -> list:
    events = []
    seen = set()

    for url in [
        "https://www.kudyznudy.cz/akce/?kraj=Praha",
        "https://www.kudyznudy.cz/akce/?kraj=Praha&strana=2",
        "https://www.kudyznudy.cz/akce/?kraj=Praha&strana=3",
    ]:
        r = get(url, delay=1.2, extra_headers={"Referer": "https://www.kudyznudy.cz/"})
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")

        # KudyZNudy marks each event card with schema.org microdata (itemprop meta tags)
        for card in soup.select('[itemtype*="schema.org/Event"], [itemtype*="schema.org/event"]'):
            title_el = card.select_one("h3 a, h2 a, .event-swiper-name a, a[title]")
            title = ""
            if title_el:
                title = title_el.get_text(strip=True) or title_el.get("title", "")
            if not title or len(title) < 5 or len(title) > 200:
                continue

            href = ""
            link_el = card.select_one("a[href]")
            if link_el:
                href = link_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.kudyznudy.cz" + href
            key = href or title
            if key in seen:
                continue
            seen.add(key)

            # Location — the carousels mix in nationwide events, so keep Prague only
            loc_el = card.select_one('[itemprop="location"]')
            location = loc_el.get("content") or loc_el.get_text(strip=True) if loc_el else ""
            location = location.strip()
            if "Praha" not in location:
                continue
            venue = location or "Praha"

            start_el = card.select_one('meta[itemprop="startDate"]')
            date_str = start_el.get("content", "") if start_el else ""
            end_el = card.select_one('meta[itemprop="endDate"]')
            end_str = end_el.get("content", "") if end_el else ""

            img_el = card.select_one('[itemprop="image"], img')
            img = ""
            if img_el:
                img = img_el.get("content") or img_el.get("src") or img_el.get("data-src") or ""

            desc_el = card.select_one('meta[itemprop="description"]')
            desc = desc_el.get("content", "")[:200] if desc_el else ""

            events.append(_ev(
                title=title, date=date_str, end=end_str, venue=venue,
                description=desc, url=href, image=img, source="KudyZNudy",
            ))

    log.info("KudyZNudy: %d events", len(events))
    return events


# ─── Source 3: Prague.eu ─────────────────────────────────────────────────────

def scrape_prague_eu() -> list:
    events = []
    for base_url in [
        "https://prague.eu/en/akce/",
        "https://prague.eu/cs/akce/",
        "https://www.prague.eu/en/events",
    ]:
        r = get(base_url, delay=1.2)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") not in ("Event", "MusicEvent", "TheaterEvent",
                                              "ExhibitionEvent", "Festival"):
                    continue
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                start = item.get("startDate", "")
                loc = item.get("location", {})
                venue = loc.get("name", "Praha") if isinstance(loc, dict) else "Praha"
                img = item.get("image", "")
                if isinstance(img, list):
                    img = img[0] if img else ""
                events.append(_ev(
                    title=name,
                    date=start, time_="",
                    venue=venue,
                    description=re.sub(r"<[^>]+>", "", item.get("description", ""))[:200],
                    url=item.get("url", ""),
                    image=img,
                    source="Prague.eu",
                ))

        for sel in ["article", ".event-card", ".event-item", "li.item",
                    "[class*=event]", "[class*=Event]"]:
            cards = soup.select(sel)
            if len(cards) < 2:
                continue
            for card in cards[:50]:
                title_el = card.select_one("h2,h3,h4,[class*=title],[class*=name]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if len(title) < 3:
                    continue
                date_el  = card.select_one("time,[class*=date],[class*=Date]")
                link_el  = card.select_one("a[href]")
                img_el   = card.select_one("img[src]")
                desc_el  = card.select_one("p,[class*=desc],[class*=perex]")
                venue_el = card.select_one("[class*=venue],[class*=location]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://prague.eu" + href
                img = img_el["src"] if img_el else ""
                if img and not img.startswith("http"):
                    img = "https://prague.eu" + img
                date_str = ""
                if date_el:
                    date_str = date_el.get("datetime") or extract_date(
                        date_el.get_text(strip=True, separator=" "))
                events.append(_ev(
                    title=title, date=date_str,
                    venue=venue_el.get_text(strip=True) if venue_el else "Praha",
                    description=desc_el.get_text(strip=True) if desc_el else "",
                    url=href, image=img, source="Prague.eu",
                ))
            if events:
                break
        if events:
            break

    log.info("Prague.eu: %d events", len(events))
    return events


# ─── Source 4: Eventbrite ─────────────────────────────────────────────────────

def scrape_eventbrite() -> list:
    events = []
    for url in [
        "https://www.eventbrite.com/d/czechia--prague/events/",
        "https://www.eventbrite.com/d/czechia--prague/things-to-do/",
    ]:
        r = get(url, delay=2.0)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") not in ("Event", "MusicEvent", "TheaterEvent",
                                              "ExhibitionEvent", "Festival", "SocialEvent"):
                    continue
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                start = item.get("startDate", "")
                loc   = item.get("location", {})
                venue = loc.get("name", "Praha") if isinstance(loc, dict) else "Praha"
                img   = item.get("image", "")
                if isinstance(img, list):
                    img = img[0] if img else ""
                desc = re.sub(r"<[^>]+>", "", item.get("description", ""))[:200]
                events.append(_ev(
                    title=name,
                    date=start, time_="",
                    venue=venue, description=desc,
                    url=item.get("url", ""), image=img, source="Eventbrite",
                ))
        for card in soup.select("[data-event-id],[class*=event-card],article[class*=event]")[:30]:
            title_el = card.select_one("h2,h3,[class*=title],[class*=name]")
            if not title_el:
                continue
            name = title_el.get_text(strip=True)
            date_el = card.select_one("time,[class*=date]")
            link_el = card.select_one("a[href]")
            img_el  = card.select_one("img")
            events.append(_ev(
                title=name,
                date=extract_date(date_el.get_text() if date_el else ""),
                url=link_el["href"] if link_el else "",
                image=img_el.get("src", "") if img_el else "",
                source="Eventbrite",
            ))
        if events:
            break

    log.info("Eventbrite: %d events", len(events))
    return events




# ─── Processing ───────────────────────────────────────────────────────────────

_GARBAGE_TITLES = {"dates", "event detail", "events", "akce", "program",
                   "vstup", "vstupenky", "koupit", "buy tickets", "tickets",
                   "more", "více", "detail", "zobrazit", "show more"}

def process(all_events: list) -> list:
    today    = datetime.now().date()
    today_s  = today.strftime("%Y-%m-%d")
    cutoff   = today + timedelta(days=90)
    valid    = []
    for ev in all_events:
        title = ev.get("title", "").strip()
        # Skip garbage: too short, or a known UI label
        if len(title) < 5 or title.lower() in _GARBAGE_TITLES:
            continue

        # Comedy isn't a standalone category — it's a subcategory of theater
        if ev.get("category") == "comedy":
            ev["category"] = "theater"
            if not ev.get("subcat"):
                ev["subcat"] = "comedy"

        start = ev.get("date", "")
        end   = ev.get("end", "")
        sd = ed = None
        if start:
            try: sd = datetime.strptime(start[:10], "%Y-%m-%d").date()
            except ValueError: sd = None
        if end:
            try: ed = datetime.strptime(end[:10], "%Y-%m-%d").date()
            except ValueError: ed = None

        if sd is None:
            # No usable date — keep, sorts to the end
            ev["_sort"] = "9999-99-99"
        else:
            eff_end = ed or sd                       # single-day if no end
            # Drop only if already finished, or doesn't start within 90 days
            if eff_end < today or sd > cutoff:
                continue
            ev["_ongoing"] = sd < today <= eff_end   # started earlier, still running
            # Sort/display from today if already ongoing, else from start date
            ev["_sort"] = today_s if ev["_ongoing"] else start[:10]
            ev["end"]   = eff_end.strftime("%Y-%m-%d")
        valid.append(ev)

    seen, out = set(), []
    for ev in valid:
        key = re.sub(r"\W", "", ev["title"].lower())[:30]
        if key and key not in seen:
            seen.add(key)
            out.append(ev)

    return sorted(out, key=lambda e: e["_sort"])


# ─── HTML template ────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Praha — Kulturní akce | Prague Cultural Events</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f0f13;--surface:#1a1a24;--surface2:#22222f;--border:#2a2a3a;
  --text:#e8e8f0;--muted:#7777aa;--accent:#c9a84c;--accent-dim:#9a7a35;
  --r:12px
}
body{background:var(--bg);color:var(--text);
  font-family:'Segoe UI',system-ui,-apple-system,sans-serif;min-height:100vh}

/* ── Header ── */
header{
  background:linear-gradient(160deg,#1a0e2e 0%,#0d1f3e 45%,#190e00 100%);
  padding:0 0 18px;text-align:center;position:relative;overflow:hidden;
  border-bottom:1px solid rgba(201,168,76,.18)
}

/* ── Hero carousel ── */
.hero{
  position:relative;width:100%;height:min(46vw,420px);min-height:260px;
  overflow:hidden;background:#0d0d16
}
.hero-track{display:flex;height:100%;transition:transform .6s cubic-bezier(.4,0,.2,1)}
.hero-slide{
  position:relative;flex:0 0 100%;height:100%;overflow:hidden;cursor:pointer;
  background:#0d0d16
}
/* Blurred, darkened copy fills the frame behind the full image (no black bars) */
.hero-bg{
  position:absolute;inset:0;background-size:cover;background-position:center;
  filter:blur(26px) brightness(.45);transform:scale(1.18)
}
/* The whole image, never cropped */
.hero-photo{
  position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:1
}
.hero-slide::after{
  content:"";position:absolute;inset:0;z-index:2;pointer-events:none;
  background:linear-gradient(to top,rgba(10,10,18,.92) 0%,rgba(10,10,18,.3) 45%,rgba(10,10,18,0) 72%)
}
.hero-caption{
  position:absolute;left:0;right:0;bottom:52px;z-index:3;
  padding:0 clamp(20px,6vw,70px);text-align:left;pointer-events:none
}
.hero-cat{
  display:inline-block;font-size:.68rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;padding:3px 9px;border-radius:4px;color:#000;margin-bottom:9px
}
.hero-new{
  display:inline-block;margin-left:7px;font-size:.66rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.06em;padding:3px 9px;border-radius:4px;
  background:#e0245e;color:#fff
}
.hero-ttl{
  font-size:clamp(1.3rem,3.4vw,2.3rem);font-weight:700;line-height:1.15;
  color:#fff;max-width:760px;text-shadow:0 2px 14px rgba(0,0,0,.6);margin-bottom:6px
}
.hero-meta{font-size:.9rem;color:#d8d8ee;text-shadow:0 1px 8px rgba(0,0,0,.7)}
.hero-nav{
  position:absolute;top:50%;transform:translateY(-50%);z-index:4;
  width:40px;height:40px;border-radius:50%;border:none;cursor:pointer;
  background:rgba(0,0,0,.4);color:#fff;font-size:1.6rem;line-height:1;
  display:flex;align-items:center;justify-content:center;transition:background .15s
}
.hero-nav:hover{background:rgba(201,168,76,.85);color:#000}
.hero-prev{left:14px}.hero-next{right:14px}
.hero-dots{
  position:absolute;bottom:16px;left:0;right:0;z-index:4;
  display:flex;gap:7px;justify-content:center
}
.hero-dot{
  width:8px;height:8px;border-radius:50%;border:none;cursor:pointer;padding:0;
  background:rgba(255,255,255,.35);transition:all .2s
}
.hero-dot.on{background:var(--accent);width:22px;border-radius:4px}
.hero-titlebar{
  position:absolute;top:14px;left:18px;z-index:4;
  display:flex;align-items:center;gap:10px
}
.hero-titlebar h1{
  font-size:clamp(1rem,2.2vw,1.45rem);font-weight:700;letter-spacing:-.01em;
  background:linear-gradient(135deg,#f5e090,#c9a84c 55%,#f0d070);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  text-shadow:0 1px 10px rgba(0,0,0,.4)
}
.hero-badge{
  font-size:.66rem;font-weight:600;color:#cfc89a;
  background:rgba(0,0,0,.35);padding:3px 9px;border-radius:12px;white-space:nowrap
}
/* Fallback when there are no featured events */
.hero.empty{height:auto;min-height:0;padding:40px 20px 10px}
.hero.empty .hero-track,.hero.empty .hero-caption,
.hero.empty .hero-nav,.hero.empty .hero-dots{display:none}
.hero.empty .hero-titlebar{position:static;justify-content:center;margin-bottom:6px}
.hero.empty .hero-titlebar h1{font-size:clamp(1.8rem,5vw,3rem)}

.subtitle{margin-top:12px;color:#8888bb;font-size:.9rem;
  letter-spacing:.07em;text-transform:uppercase;position:relative;z-index:1}
.updated{margin-top:8px;font-size:.75rem;color:#55557a;position:relative;z-index:1}

/* ── Language switcher ── */
.lang-bar{
  position:absolute;top:14px;right:18px;
  display:flex;gap:4px;z-index:10
}
.lang-btn{
  background:rgba(0,0,0,.35);
  border:1px solid rgba(255,255,255,.18);
  color:#ccccdd;padding:3px 9px;border-radius:14px;
  font-size:.72rem;font-weight:700;letter-spacing:.04em;
  cursor:pointer;transition:all .15s
}
.lang-btn:hover{background:rgba(0,0,0,.6);color:#fff}
.lang-btn.active{background:var(--accent);border-color:transparent;color:#000}

/* ── Filters bar ── */
.fbar{
  display:flex;flex-wrap:wrap;gap:7px;padding:14px 20px;
  background:var(--surface);border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:100
}
.fbtn{
  border:1px solid var(--border);padding:4px 12px;border-radius:20px;
  font-size:.8rem;font-weight:500;cursor:pointer;transition:all .15s;
  background:var(--surface2);color:var(--muted)
}
.fbtn:hover{background:var(--border);color:var(--text)}
.fbtn.on{color:#000;border-color:transparent;font-weight:600}
.fcnt{font-size:.7em;opacity:.7;margin-left:2px}
/* second-tier subcategory bar — only shown when a category with subcats is active */
.subbar{
  display:none;flex-wrap:wrap;gap:6px;padding:10px 20px;
  background:var(--bg);border-bottom:1px solid var(--border);
  position:sticky;top:49px;z-index:99
}
.subbar.show{display:flex}
.sbtn{
  border:1px solid var(--border);padding:3px 11px;border-radius:16px;
  font-size:.76rem;font-weight:500;cursor:pointer;transition:all .15s;
  background:transparent;color:var(--muted)
}
.sbtn:hover{background:var(--surface2);color:var(--text)}
.sbtn.on{color:#000;font-weight:600;border-color:transparent}
/* ── Search bar ── */
.searchbar{
  display:flex;align-items:center;gap:8px;padding:12px 20px;
  background:var(--surface);border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:101
}
.search-ico{font-size:.95rem;opacity:.7}
#search{
  flex:1;min-width:0;background:var(--bg);border:1px solid var(--border);
  color:var(--text);padding:9px 13px;border-radius:10px;font-size:.95rem;outline:none
}
#search:focus{border-color:var(--accent)}
#search::placeholder{color:var(--muted)}
.search-clear{
  background:none;border:none;color:var(--muted);font-size:1rem;cursor:pointer;
  padding:4px 8px;border-radius:6px
}
.search-clear:hover{background:var(--surface2);color:var(--text)}
.saved-toggle{
  background:var(--surface2);border:1px solid var(--border);color:var(--muted);
  padding:8px 13px;border-radius:10px;font-size:.9rem;cursor:pointer;white-space:nowrap;
  transition:all .15s
}
.saved-toggle:hover{color:#e0245e;border-color:#e0245e}
.saved-toggle.on{background:#e0245e;border-color:transparent;color:#fff}
/* when search is sticky, the filter bars sit just below it */
.fbar{top:63px}
.subbar{top:111px}
/* ── Card save / calendar actions ── */
.card-actions{display:flex;gap:6px;align-items:center}
.card-act{
  background:none;border:1px solid var(--border);color:var(--muted);
  width:30px;height:28px;border-radius:7px;cursor:pointer;font-size:.9rem;
  display:flex;align-items:center;justify-content:center;transition:all .15s;padding:0
}
.card-act:hover{background:var(--surface2);color:var(--text)}
.card-act.saved{color:#e0245e;border-color:#e0245e}

/* ── Layout ── */
.layout{display:flex;max-width:1500px;margin:0 auto}

/* ── Calendar sidebar ── */
.cal-wrap{
  width:280px;flex-shrink:0;padding:20px 0 20px 20px;
  position:sticky;top:53px;height:calc(100vh - 53px);overflow-y:auto
}
.cal-box{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
  padding:16px;user-select:none
}
.cal-nav{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:12px
}
.cal-nav span{font-weight:600;font-size:.95rem;color:var(--accent)}
.cal-nav button{
  background:none;border:1px solid var(--border);color:var(--muted);
  width:28px;height:28px;border-radius:6px;cursor:pointer;font-size:1rem;
  display:flex;align-items:center;justify-content:center;transition:all .12s
}
.cal-nav button:hover{background:var(--border);color:var(--text)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;font-size:.78rem}
.cal-dh{text-align:center;color:var(--muted);font-weight:600;
  padding:4px 0;font-size:.7rem}
.cal-day{
  text-align:center;padding:5px 0 3px;border-radius:6px;cursor:pointer;
  position:relative;transition:all .12s;line-height:1.2
}
.cal-day:hover{background:var(--border)}
.cal-day.empty,.cal-day.past{color:#444466;cursor:default}
.cal-day.past:hover{background:none}
.cal-day.has-ev{color:var(--text);font-weight:500}
.cal-day.today{color:var(--accent);font-weight:700}
.cal-dot{display:block;width:4px;height:4px;border-radius:50%;
  background:var(--accent);margin:2px auto 0}
.cal-day.selected{background:var(--accent)!important;color:#000!important;font-weight:700}
.cal-day.selected .cal-dot{background:#000}
.cal-clear{
  display:block;width:100%;margin-top:12px;padding:7px;
  border:1px solid var(--border);border-radius:8px;background:none;
  color:var(--muted);cursor:pointer;font-size:.8rem;transition:all .12s
}
.cal-clear:hover{background:var(--border);color:var(--text)}
.cal-legend{margin-top:14px;padding-top:12px;border-top:1px solid var(--border)}
.cal-legend-title{font-size:.7rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:8px}
.leg-item{display:flex;align-items:center;gap:7px;margin-bottom:5px;
  font-size:.78rem;cursor:pointer;padding:3px 5px;border-radius:5px;transition:.12s}
.leg-item:hover{background:var(--surface2)}
.leg-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}

/* ── Events area ── */
.events-area{flex:1;padding:20px;min-width:0}
.sec-head{
  font-size:.7rem;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  color:var(--accent);padding:16px 0 10px;border-bottom:1px solid var(--border);
  margin-bottom:14px
}
.sec-rec{font-size:.95rem;letter-spacing:.04em;color:#f0d070;border-bottom-color:rgba(201,168,76,.4)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  gap:18px;margin-bottom:6px}
.sel-header{
  display:flex;align-items:center;gap:12px;margin-bottom:20px;
  padding:12px 16px;background:var(--surface);border-radius:var(--r);
  border:1px solid var(--border)
}
.sel-header span{font-size:1.05rem;font-weight:600;color:var(--accent)}
.sel-header button{
  margin-left:auto;padding:5px 12px;border:1px solid var(--border);
  border-radius:6px;background:none;color:var(--muted);cursor:pointer;
  font-size:.8rem;transition:all .12s
}
.sel-header button:hover{background:var(--border);color:var(--text)}

/* ── Cards ── */
.card{
  background:var(--surface);border-radius:var(--r);overflow:hidden;
  border:1px solid var(--border);transition:transform .15s,box-shadow .15s;
  display:flex;flex-direction:column
}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 28px rgba(0,0,0,.45)}
.card-img{width:100%;height:148px;object-fit:cover;display:block;flex-shrink:0}
.card-ph{width:100%;height:148px;display:flex;align-items:center;
  justify-content:center;font-size:2.6rem;flex-shrink:0}
.card-body{padding:12px 14px 14px;flex:1;display:flex;flex-direction:column}
.card-meta{display:flex;align-items:center;gap:7px;margin-bottom:7px;flex-wrap:wrap}
.badge{font-size:.66rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;padding:2px 7px;border-radius:4px;color:#000;white-space:nowrap}
.card-date{font-size:.76rem;color:var(--muted)}
.card-title{font-size:.94rem;font-weight:600;line-height:1.35;margin-bottom:5px}
.card-venue{font-size:.78rem;color:var(--muted);margin-bottom:7px}
.card-desc{font-size:.78rem;color:#6666aa;line-height:1.5;flex:1;margin-bottom:11px;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card-foot{display:flex;align-items:center;justify-content:space-between;margin-top:auto}
.card-src{font-size:.68rem;color:var(--muted);opacity:.55}
.card-lnk{font-size:.76rem;color:var(--accent);text-decoration:none;font-weight:600;
  padding:3px 9px;border:1px solid var(--accent-dim);border-radius:5px;
  transition:all .15s;white-space:nowrap}
.card-lnk:hover{background:var(--accent);color:#000}
.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty span{font-size:2.5rem;display:block;margin-bottom:8px}

/* ── Footer ── */
footer{text-align:center;padding:24px;color:var(--muted);font-size:.76rem;
  border-top:1px solid var(--border);margin-top:30px}
footer a{color:var(--accent);text-decoration:none}
code{background:var(--surface2);padding:1px 5px;border-radius:3px;font-size:.85em}

/* ── Responsive ── */
@media(max-width:900px){
  .layout{flex-direction:column}
  /* events first, calendar below — so people see events immediately on a phone */
  .events-area{order:1;padding:14px}
  .cal-wrap{order:2;width:100%;padding:0 14px 14px;position:static;height:auto}
  .grid{grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
  .lang-bar{top:10px;right:10px}
  .lang-btn{padding:2px 7px;font-size:.68rem}
  /* filter rows scroll sideways instead of stacking into tall blocks */
  .fbar,.subbar{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;
    position:static;scrollbar-width:none}
  .fbar::-webkit-scrollbar,.subbar::-webkit-scrollbar{display:none}
  .fbtn,.sbtn{white-space:nowrap;flex:0 0 auto}
  .searchbar{top:0}
}
@media(max-width:600px){
  .hero{min-height:200px;height:56vw}
  .searchbar{padding:10px 12px;gap:6px}
  #search{padding:8px 11px;font-size:.9rem}
  .saved-toggle{padding:7px 10px;font-size:.82rem}
  .grid{grid-template-columns:1fr}
  .card-img,.card-ph{height:160px}
}
</style>
</head>
<body>

<header>
  <!-- Language switcher — top right -->
  <div class="lang-bar">
    <button class="lang-btn active" data-lang="cs" onclick="setLang('cs')">CZ</button>
    <button class="lang-btn" data-lang="en" onclick="setLang('en')">EN</button>
    <button class="lang-btn" data-lang="ru" onclick="setLang('ru')">RU</button>
    <button class="lang-btn" data-lang="uk" onclick="setLang('uk')">UA</button>
    <button class="lang-btn" data-lang="vi" onclick="setLang('vi')">VI</button>
  </div>

  <!-- Hero carousel of events announced this week -->
  <div class="hero" id="hero">
    <div class="hero-track" id="hero-track"></div>
    <div class="hero-caption" id="hero-caption"></div>
    <button class="hero-nav hero-prev" id="hero-prev" aria-label="Prev">&#8249;</button>
    <button class="hero-nav hero-next" id="hero-next" aria-label="Next">&#8250;</button>
    <div class="hero-dots" id="hero-dots"></div>
    <div class="hero-titlebar">
      <h1 id="hdr-title">Praha — Kulturní akce</h1>
      <span class="hero-badge" id="hero-badge"></span>
    </div>
  </div>

  <p class="subtitle" id="hdr-subtitle">Kulturní akce v Praze</p>
  <div class="updated" id="updated-line" data-ts="__UPDATED__"></div>
</header>

<div class="searchbar">
  <span class="search-ico">🔍</span>
  <input type="search" id="search" autocomplete="off" placeholder="Search events…"
         oninput="onSearch(this.value)">
  <button class="search-clear" id="search-clear" onclick="clearSearch()" style="display:none">✕</button>
  <button class="saved-toggle" id="saved-toggle" onclick="toggleSaved()">♥ <span id="saved-count">0</span></button>
</div>

<nav class="fbar" id="fbar" aria-label="Filter"></nav>
<nav class="subbar" id="subbar" aria-label="Subcategory"></nav>

<div class="layout">
  <aside class="cal-wrap">
    <div class="cal-box">
      <div class="cal-nav">
        <button id="cal-prev">&#8592;</button>
        <span id="cal-title"></span>
        <button id="cal-next">&#8594;</button>
      </div>
      <div class="cal-grid" id="cal-grid"></div>
      <button class="cal-clear" id="cal-clear" style="display:none" onclick="selectDate(null)"></button>
      <div class="cal-legend" id="cal-legend"></div>
    </div>
  </aside>
  <main class="events-area" id="events-area"></main>
</div>

<footer>
  <span id="footer-sources"></span>
  <br><br>
  <span id="footer-hint"></span>
</footer>

<script>
const EVENTS = __EVENTS_JSON__;
const FEATURED = __FEATURED_JSON__;
const COLORS = __COLORS_JSON__;
const ICONS  = __ICONS_JSON__;

// Subcategory labels (5 languages). Keys match the scraper's _GOOUT_SUBCAT values.
const SUBCAT_LABELS = {
  // music
  rock:{cs:"Rock",en:"Rock",ru:"Рок",uk:"Рок",vi:"Rock"},
  pop:{cs:"Pop",en:"Pop",ru:"Поп",uk:"Поп",vi:"Pop"},
  classical:{cs:"Klasická",en:"Classical",ru:"Классика",uk:"Класика",vi:"Cổ điển"},
  jazz:{cs:"Jazz",en:"Jazz",ru:"Джаз",uk:"Джаз",vi:"Jazz"},
  electronic:{cs:"Elektronická",en:"Electronic",ru:"Электронная",uk:"Електронна",vi:"Điện tử"},
  folk:{cs:"Folk",en:"Folk",ru:"Фолк",uk:"Фолк",vi:"Dân gian"},
  punk:{cs:"Punk",en:"Punk",ru:"Панк",uk:"Панк",vi:"Punk"},
  metal:{cs:"Metal",en:"Metal",ru:"Метал",uk:"Метал",vi:"Metal"},
  rap:{cs:"Rap / Hip-hop",en:"Rap / Hip-hop",ru:"Рэп / Хип-хоп",uk:"Реп / Хіп-хоп",vi:"Rap / Hip-hop"},
  alternative:{cs:"Alternativa",en:"Alternative",ru:"Альтернатива",uk:"Альтернатива",vi:"Alternative"},
  blues:{cs:"Blues",en:"Blues",ru:"Блюз",uk:"Блюз",vi:"Blues"},
  world:{cs:"World",en:"World",ru:"World",uk:"World",vi:"World"},
  // theater
  comedy:{cs:"Komedie",en:"Comedy",ru:"Комедия",uk:"Комедія",vi:"Hài kịch"},
  drama:{cs:"Drama",en:"Drama",ru:"Драма",uk:"Драма",vi:"Chính kịch"},
  puppets:{cs:"Loutky",en:"Puppets",ru:"Кукольный",uk:"Ляльковий",vi:"Múa rối"},
  performance:{cs:"Performance",en:"Performance",ru:"Перформанс",uk:"Перформанс",vi:"Trình diễn"},
  musical:{cs:"Muzikál",en:"Musical",ru:"Мюзикл",uk:"Мюзикл",vi:"Nhạc kịch"},
  opera:{cs:"Opera",en:"Opera",ru:"Опера",uk:"Опера",vi:"Opera"},
  ballet:{cs:"Balet",en:"Ballet",ru:"Балет",uk:"Балет",vi:"Ballet"},
  // film
  thriller:{cs:"Thriller",en:"Thriller",ru:"Триллер",uk:"Трилер",vi:"Giật gân"},
  animated:{cs:"Animovaný",en:"Animated",ru:"Анимация",uk:"Анімація",vi:"Hoạt hình"},
  action:{cs:"Akční",en:"Action",ru:"Боевик",uk:"Бойовик",vi:"Hành động"},
  romantic:{cs:"Romantický",en:"Romantic",ru:"Романтика",uk:"Романтика",vi:"Lãng mạn"},
  historic:{cs:"Historický",en:"Historical",ru:"Исторический",uk:"Історичний",vi:"Lịch sử"},
  adventure:{cs:"Dobrodružný",en:"Adventure",ru:"Приключения",uk:"Пригоди",vi:"Phiêu lưu"},
  documentary:{cs:"Dokument",en:"Documentary",ru:"Документальный",uk:"Документальний",vi:"Tài liệu"},
  horror:{cs:"Horor",en:"Horror",ru:"Ужасы",uk:"Жахи",vi:"Kinh dị"},
  // exhibitions
  history:{cs:"Historie",en:"History",ru:"История",uk:"Історія",vi:"Lịch sử"},
  contemporary:{cs:"Současné",en:"Contemporary",ru:"Современное",uk:"Сучасне",vi:"Đương đại"},
  paintings:{cs:"Malba",en:"Paintings",ru:"Живопись",uk:"Живопис",vi:"Hội họa"},
  photography:{cs:"Fotografie",en:"Photography",ru:"Фотография",uk:"Фотографія",vi:"Nhiếp ảnh"},
  // family
  educational:{cs:"Vzdělávací",en:"Educational",ru:"Образовательные",uk:"Освітні",vi:"Giáo dục"},
  fairytale:{cs:"Pohádky",en:"Fairy tales",ru:"Сказки",uk:"Казки",vi:"Cổ tích"},
  toddlers:{cs:"Pro nejmenší",en:"Toddlers",ru:"Для малышей",uk:"Для найменших",vi:"Trẻ nhỏ"},
};

// ── Translations ────────────────────────────────────────────

const LANG = {
  cs: {
    title: "Praha — Kulturní akce",
    subtitle: "Kulturní akce v Praze",
    updated: "Aktualizováno",
    all: "Vše",
    moreInfo: "Více info →",
    until: "Do",
    featured: "Oznámeno tento týden",
    recommended: "Doporučené tento týden",
    popular: "Populární",
    searchPh: "Hledat akce…",
    save: "Uložit", addCal: "Přidat do kalendáře",
    savedTitle: "Uložené akce", noSaved: "Zatím žádné uložené akce",
    allIn: "Vše:",
    noEvents: "Žádné akce nenalezeny",
    showAll: "✕ Zobrazit vše",
    categories: "Kategorie",
    today: "Dnes", tomorrow: "Zítra",
    thisWeek: "Tento týden", nextWeek: "Příští týden",
    thisMonth: "Tento měsíc", comingUp: "Brzy", noDate: "Bez data",
    months: ["Leden","Unor","Brezen","Duben","Kveten","Cerven","Cervenec","Srpen","Zari","Rijen","Listopad","Prosinec"],
    monthsOf: ["ledna","února","března","dubna","května","června","července","srpna","září","října","listopadu","prosince"],
    days: ["Po","Út","St","Čt","Pá","So","Ne"],
    cats: { cinema:"Kino", theater:"Divadlo", standup:"Stand-up", concert:"Hudba", festival:"Festival",
            exhibition:"Výstava", outdoor:"Open Air", comedy:"Komedie", dance:"Tanec",
            family:"Pro děti", sports:"Sport", other:"Ostatní" },
    sources: "Zdroje",
    hint: "Pro aktualizaci spusťte run_update.bat nebo nastavte automatické spouštění přes Windows Task Scheduler."
  },
  en: {
    title: "Prague — Cultural Events",
    subtitle: "Cultural Events in Prague",
    updated: "Updated",
    all: "All",
    moreInfo: "More info →",
    until: "Until",
    featured: "Announced this week",
    recommended: "Recommended this week",
    popular: "Popular",
    searchPh: "Search events…",
    save: "Save", addCal: "Add to calendar",
    savedTitle: "Saved events", noSaved: "No saved events yet",
    allIn: "All:",
    noEvents: "No events found",
    showAll: "✕ Show all",
    categories: "Categories",
    today: "Today", tomorrow: "Tomorrow",
    thisWeek: "This Week", nextWeek: "Next Week",
    thisMonth: "This Month", comingUp: "Coming Up", noDate: "No date",
    months: ["January","February","March","April","May","June","July","August","September","October","November","December"],
    monthsOf: ["January","February","March","April","May","June","July","August","September","October","November","December"],
    days: ["Mo","Tu","We","Th","Fr","Sa","Su"],
    cats: { cinema:"Cinema", theater:"Theater", standup:"Stand-up", concert:"Music", festival:"Festival",
            exhibition:"Exhibition", outdoor:"Open Air", comedy:"Comedy", dance:"Dance",
            family:"Family", sports:"Sports", other:"Other" },
    sources: "Sources",
    hint: "To update run run_update.bat, or set up automatic daily runs via Windows Task Scheduler."
  },
  ru: {
    title: "Прага — Культурные события",
    subtitle: "Культурные мероприятия в Праге",
    updated: "Обновлено",
    all: "Все",
    moreInfo: "Подробнее →",
    until: "До",
    featured: "Анонсировано на этой неделе",
    recommended: "Рекомендуем на этой неделе",
    popular: "Популярное",
    searchPh: "Поиск событий…",
    save: "Сохранить", addCal: "В календарь",
    savedTitle: "Сохранённые события", noSaved: "Пока нет сохранённых событий",
    allIn: "Все:",
    noEvents: "Мероприятий не найдено",
    showAll: "✕ Показать все",
    categories: "Категории",
    today: "Сегодня", tomorrow: "Завтра",
    thisWeek: "На этой неделе", nextWeek: "На следующей неделе",
    thisMonth: "В этом месяце", comingUp: "Скоро", noDate: "Без даты",
    months: ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"],
    monthsOf: ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"],
    days: ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"],
    cats: { cinema:"Кино", theater:"Театр", standup:"Стендап", concert:"Музыка", festival:"Фестиваль",
            exhibition:"Выставка", outdoor:"На улице", comedy:"Комедия", dance:"Танцы",
            family:"Для детей", sports:"Спорт", other:"Другое" },
    sources: "Источники",
    hint: "Для обновления запустите run_update.bat."
  },
  uk: {
    title: "Прага — Культурні події",
    subtitle: "Культурні заходи у Празі",
    updated: "Оновлено",
    all: "Всі",
    moreInfo: "Детальніше →",
    until: "До",
    featured: "Анонсовано цього тижня",
    recommended: "Рекомендовано цього тижня",
    popular: "Популярне",
    searchPh: "Пошук подій…",
    save: "Зберегти", addCal: "До календаря",
    savedTitle: "Збережені події", noSaved: "Поки немає збережених подій",
    allIn: "Все:",
    noEvents: "Заходів не знайдено",
    showAll: "✕ Показати все",
    categories: "Категорії",
    today: "Сьогодні", tomorrow: "Завтра",
    thisWeek: "Цього тижня", nextWeek: "Наступного тижня",
    thisMonth: "Цього місяця", comingUp: "Незабаром", noDate: "Без дати",
    months: ["Січень","Лютий","Березень","Квітень","Травень","Червень","Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"],
    monthsOf: ["січня","лютого","березня","квітня","травня","червня","липня","серпня","вересня","жовтня","листопада","грудня"],
    days: ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"],
    cats: { cinema:"Кіно", theater:"Театр", standup:"Стендап", concert:"Музика", festival:"Фестиваль",
            exhibition:"Виставка", outdoor:"Просто неба", comedy:"Комедія", dance:"Танці",
            family:"Для дітей", sports:"Спорт", other:"Інше" },
    sources: "Джерела",
    hint: "Для оновлення запустіть run_update.bat."
  },
  vi: {
    title: "Praha — Sự Kiện Văn Hóa",
    subtitle: "Sự kiện văn hóa tại Praha",
    updated: "Cập nhật",
    all: "Tất cả",
    moreInfo: "Xem thêm →",
    until: "Đến",
    featured: "Công bố trong tuần này",
    recommended: "Đề xuất trong tuần này",
    popular: "Phổ biến",
    searchPh: "Tìm sự kiện…",
    save: "Lưu", addCal: "Thêm vào lịch",
    savedTitle: "Sự kiện đã lưu", noSaved: "Chưa có sự kiện đã lưu",
    allIn: "Tất cả:",
    noEvents: "Không tìm thấy sự kiện",
    showAll: "✕ Hiển thị tất cả",
    categories: "Danh mục",
    today: "Hôm nay", tomorrow: "Ngày mai",
    thisWeek: "Tuần này", nextWeek: "Tuần tới",
    thisMonth: "Tháng này", comingUp: "Sắp tới", noDate: "Không có ngày",
    months: ["Tháng 1","Tháng 2","Tháng 3","Tháng 4","Tháng 5","Tháng 6","Tháng 7","Tháng 8","Tháng 9","Tháng 10","Tháng 11","Tháng 12"],
    monthsOf: ["Tháng 1","Tháng 2","Tháng 3","Tháng 4","Tháng 5","Tháng 6","Tháng 7","Tháng 8","Tháng 9","Tháng 10","Tháng 11","Tháng 12"],
    days: ["T2","T3","T4","T5","T6","T7","CN"],
    cats: { cinema:"Rạp chiếu phim", theater:"Nhà hát", standup:"Stand-up", concert:"Âm nhạc", festival:"Lễ hội",
            exhibition:"Triển lãm", outdoor:"Ngoài trời", comedy:"Hài kịch", dance:"Khiêu vũ",
            family:"Gia đình", sports:"Thể thao", other:"Khác" },
    sources: "Nguồn",
    hint: "Chạy run_update.bat để cập nhật dữ liệu."
  }
};

// ── State ───────────────────────────────────────────────────

let lang = localStorage.getItem('pg-lang') || 'cs';
let activeCat = 'all';
let activeSubcat = '';
let selectedDate = null;
let calYear, calMonth;
let searchQuery = '';
let showSaved = false;
let saved = new Set(JSON.parse(localStorage.getItem('pg-saved') || '[]'));

// Stable key for saving/identifying an event
function evKey(ev) { return ev.url || (ev.title + '|' + ev.date); }

function onSearch(v) {
  searchQuery = v.trim().toLowerCase();
  document.getElementById('search-clear').style.display = v ? 'block' : 'none';
  renderEvents();
}
function clearSearch() {
  searchQuery = '';
  const el = document.getElementById('search');
  el.value = ''; el.focus();
  document.getElementById('search-clear').style.display = 'none';
  renderEvents();
}
function toggleSaved() {
  showSaved = !showSaved;
  document.getElementById('saved-toggle').classList.toggle('on', showSaved);
  renderEvents();
}
function toggleSave(key, btn) {
  if (saved.has(key)) saved.delete(key); else saved.add(key);
  localStorage.setItem('pg-saved', JSON.stringify([...saved]));
  updateSavedCount();
  if (btn) {
    const on = saved.has(key);
    btn.classList.toggle('saved', on);
    btn.textContent = on ? '♥' : '♡';
  }
  if (showSaved) renderEvents();   // removing from the saved view updates the list
}
function updateSavedCount() {
  document.getElementById('saved-count').textContent = saved.size;
}

// Build and download an .ics calendar file for an event
function addToCalendar(key) {
  const ev = EVENTS.find(e => evKey(e) === key);
  if (!ev || !ev.date || ev.date === '9999-99-99') return;
  const d = ev.date.replace(/-/g, '');
  const endD = (ev.end && ev.end >= ev.date ? ev.end : ev.date).replace(/-/g, '');
  // all-day event; DTEND is exclusive so add one day
  const endDt = new Date(endD.slice(0,4), +endD.slice(4,6)-1, +endD.slice(6,8)+1);
  const dtEnd = endDt.getFullYear() +
    String(endDt.getMonth()+1).padStart(2,'0') + String(endDt.getDate()).padStart(2,'0');
  const esc2 = s => String(s||'').replace(/([,;\\])/g,'\\$1').replace(/\n/g,'\\n');
  const ics = [
    'BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Prague Events//EN','BEGIN:VEVENT',
    'UID:' + d + '-' + Math.abs(hashStr(key)) + '@prague-events',
    'DTSTART;VALUE=DATE:' + d, 'DTEND;VALUE=DATE:' + dtEnd,
    'SUMMARY:' + esc2(ev.title),
    'LOCATION:' + esc2(ev.venue),
    'DESCRIPTION:' + esc2((ev.description || '') + (ev.url ? '\n' + ev.url : '')),
    ev.url ? 'URL:' + esc2(ev.url) : '',
    'END:VEVENT','END:VCALENDAR'
  ].filter(Boolean).join('\r\n');
  const blob = new Blob([ics], {type:'text/calendar'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (ev.title.replace(/[^\w ]+/g,'').slice(0,40) || 'event') + '.ics';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}
function hashStr(s){let h=0;for(let i=0;i<s.length;i++){h=(h<<5)-h+s.charCodeAt(i)|0;}return h;}

function subcatLabel(key) {
  const s = SUBCAT_LABELS[key];
  return (s && (s[lang] || s.en)) || key;
}

// Subcategories actually present for a category, with counts, most common first
function subcatsFor(cat) {
  const c = {};
  EVENTS.forEach(e => { if (e.category===cat && e.subcat) c[e.subcat]=(c[e.subcat]||0)+1; });
  return Object.entries(c).sort((a,b)=>b[1]-a[1]);
}

// ── Helpers ─────────────────────────────────────────────────

const T = () => LANG[lang];

function esc(s) {
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDate(s) {
  if (!s || s === '9999-99-99') return '';
  const parts = s.split('-').map(Number);
  if (parts.length !== 3 || parts.some(n => isNaN(n))) return '';
  const [y,m,d] = parts;
  if (m < 1 || m > 12 || d < 1 || d > 31) return '';
  const dt = new Date(y,m-1,d);
  const today = new Date(); today.setHours(0,0,0,0);
  const tom   = new Date(today); tom.setDate(today.getDate()+1);
  if (dt.toDateString() === today.toDateString()) return T().today;
  if (dt.toDateString() === tom.toDateString())   return T().tomorrow;
  const dow = (dt.getDay()+6)%7;
  return T().days[dow] + ' ' + d + '. ' + T().monthsOf[m-1];
}

function fmtDateLong(s) {
  if (!s) return '';
  const parts = s.split('-').map(Number);
  if (parts.length !== 3 || parts.some(n => isNaN(n))) return s;
  const [y,m,d] = parts;
  if (m < 1 || m > 12 || d < 1 || d > 31) return s;
  const dt = new Date(y,m-1,d);
  const dow = (dt.getDay()+6)%7;
  return T().days[dow] + ' ' + d + '. ' + T().monthsOf[m-1] + ' ' + y;
}

// Short "D. M." form for range endpoints
function fmtShort(s) {
  if (!s) return '';
  const p = s.split('-').map(Number);
  if (p.length !== 3 || p.some(n => isNaN(n))) return '';
  return p[2] + '. ' + p[1] + '.';
}

// Display string for a card: single day (Today/Tomorrow/date) or a date range
function fmtWhen(ev) {
  const end = ev.end && ev.end > ev.date ? ev.end : '';
  if (end) {
    const today = new Date(); today.setHours(0,0,0,0);
    const [sy,sm,sd] = ev.date.split('-').map(Number);
    const startDt = new Date(sy,sm-1,sd);
    if (startDt < today) return T().until + ' ' + fmtShort(end);  // ongoing
    return fmtShort(ev.date) + ' – ' + fmtShort(end);
  }
  return fmtDate(ev.date);
}

function weekBucket(s) {
  if (!s || s === '9999-99-99') return 'noDate';
  const [y,m,d] = s.split('-').map(Number);
  const dt = new Date(y,m-1,d);
  const today = new Date(); today.setHours(0,0,0,0);
  const diff = Math.floor((dt - today) / 86400000);
  if (diff <= 0)  return 'today';
  if (diff <= 1)  return 'tomorrow';
  if (diff <= 7)  return 'thisWeek';
  if (diff <= 14) return 'nextWeek';
  if (diff <= 31) return 'thisMonth';
  return 'comingUp';
}

function catLabel(cat) {
  return (T().cats && T().cats[cat]) || cat;
}

// ── Language switch ─────────────────────────────────────────

function setLang(code) {
  lang = code;
  localStorage.setItem('pg-lang', code);

  document.querySelectorAll('.lang-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === code);
  });

  const L = T();
  document.getElementById('hdr-title').textContent    = L.title;
  document.getElementById('hdr-subtitle').textContent = L.subtitle;
  document.getElementById('search').placeholder = L.searchPh;
  updateSavedCount();
  const ts = document.getElementById('updated-line').dataset.ts;
  document.getElementById('updated-line').textContent = L.updated + ': ' + ts;

  // Footer
  document.getElementById('footer-sources').innerHTML =
    L.sources + ': ' +
    '<a href="https://goout.net/cs/praha/akce/leznyvlkk/" target="_blank">GoOut.net</a> · ' +
    '<a href="https://www.kudyznudy.cz/akce/?kraj=Praha" target="_blank">KudyZNudy</a> · ' +
    '<a href="https://www.eventbrite.com/d/czechia--prague/events/" target="_blank">Eventbrite</a>';
  document.getElementById('footer-hint').innerHTML =
    'Pro aktualizaci / To update: <code>run_update.bat</code>';

  buildFilters();
  renderCalendar();
  renderEvents();
  renderHero();
}

// ── Hero carousel (events announced this week) ──────────────

let heroIdx = 0, heroTimer = null, heroSlides = [];

// Most popular events for the current category/subcategory selection.
// Ranked: recommended first, then by venue following, then soonest. Images only.
function popularSlides() {
  const today = new Date(); today.setHours(0,0,0,0);
  const t0 = today.toISOString().slice(0,10);
  let pool = EVENTS.filter(e => e.image && e.date && e.date !== '9999-99-99');
  if (activeCat !== 'all') pool = pool.filter(e => e.category === activeCat);
  if (activeSubcat)        pool = pool.filter(e => e.subcat === activeSubcat);
  // upcoming or still ongoing
  pool = pool.filter(e => {
    const end = e.end && e.end >= e.date ? e.end : e.date;
    return end >= t0;
  });
  pool.sort((a,b) =>
    (b.recommended - a.recommended) ||
    ((b.popularity||0) - (a.popularity||0)) ||
    (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return pool.slice(0, 8);
}

function heroBadgeText() {
  const L = T();
  if (activeSubcat) return '★ ' + L.popular + ': ' + subcatLabel(activeSubcat);
  if (activeCat !== 'all') return '★ ' + L.popular + ': ' + catLabel(activeCat);
  return '★ ' + L.recommended;
}

function renderHero() {
  const hero = document.getElementById('hero');
  heroSlides = popularSlides();

  if (!heroSlides.length) {
    hero.classList.add('empty');            // fall back to plain title bar
    return;
  }
  hero.classList.remove('empty');

  const track = document.getElementById('hero-track');
  track.innerHTML = heroSlides.map(ev => {
    const color = COLORS[ev.category] || '#7f8c8d';
    const click = ev.url ? `onclick="window.open('${esc(ev.url)}','_blank')"` : '';
    if (ev.image) {
      const u = esc(ev.image);
      return `<div class="hero-slide" ${click}>
        <div class="hero-bg" style="background-image:url('${u}')"></div>
        <img class="hero-photo" src="${u}" alt="" loading="lazy">
      </div>`;
    }
    return `<div class="hero-slide" style="background:linear-gradient(135deg,${color},#15151f)" ${click}></div>`;
  }).join('');

  renderDots();
  showSlide(0);
  startAuto();
}

function renderDots() {
  const dots = document.getElementById('hero-dots');
  dots.innerHTML = heroSlides.map((_, i) =>
    `<button class="hero-dot" onclick="showSlide(${i},true)"></button>`).join('');
}

function showSlide(i, user) {
  if (!heroSlides.length) return;
  heroIdx = (i + heroSlides.length) % heroSlides.length;
  const L = T();
  document.getElementById('hero-track').style.transform =
    `translateX(-${heroIdx * 100}%)`;

  const ev = heroSlides[heroIdx];
  const color = COLORS[ev.category] || '#7f8c8d';
  const icon  = ICONS[ev.category] || '⭐';
  const label = (L.cats && L.cats[ev.category]) || ev.category;
  document.getElementById('hero-caption').innerHTML = `
    <span class="hero-cat" style="background:${color}">${icon} ${esc(label)}</span>
    <span class="hero-new">${esc(heroBadgeText())}</span>
    <div class="hero-ttl">${esc(ev.title)}</div>
    <div class="hero-meta">${esc(fmtWhen(ev))}${ev.venue ? ' · 📍 ' + esc(ev.venue) : ''}</div>`;

  document.querySelectorAll('.hero-dot').forEach((d, j) =>
    d.classList.toggle('on', j === heroIdx));
  if (user) startAuto();   // reset timer on manual nav
}

function startAuto() {
  clearInterval(heroTimer);
  if (heroSlides.length > 1) heroTimer = setInterval(() => showSlide(heroIdx + 1), 5000);
}

document.getElementById('hero-prev').onclick = () => showSlide(heroIdx - 1, true);
document.getElementById('hero-next').onclick = () => showSlide(heroIdx + 1, true);
document.getElementById('hero').addEventListener('mouseenter', () => clearInterval(heroTimer));
document.getElementById('hero').addEventListener('mouseleave', startAuto);

// ── Calendar ────────────────────────────────────────────────

// Does event ev occur on calendar day `dayStr` (YYYY-MM-DD)? Range-aware.
function eventOnDay(ev, dayStr) {
  if (!ev.date || ev.date === '9999-99-99') return false;
  const end = ev.end && ev.end >= ev.date ? ev.end : ev.date;
  return ev.date <= dayStr && dayStr <= end;
}

function getEventDatesMap() {
  const map = {};
  const today = new Date(); today.setHours(0,0,0,0);
  const horizon = new Date(today); horizon.setDate(today.getDate()+120);
  EVENTS.forEach(ev => {
    if (!ev.date || ev.date === '9999-99-99') return;
    const [sy,sm,sd] = ev.date.split('-').map(Number);
    let cur = new Date(sy, sm-1, sd);
    if (cur < today) cur = new Date(today);     // don't dot past days
    const endStr = ev.end && ev.end >= ev.date ? ev.end : ev.date;
    const [ey,em,ed] = endStr.split('-').map(Number);
    const endDt = new Date(ey, em-1, ed);
    // Mark each day in the range (capped) so multi-day events dot every day
    let guard = 0;
    while (cur <= endDt && cur <= horizon && guard++ < 400) {
      const key = cur.getFullYear() + '-' +
        String(cur.getMonth()+1).padStart(2,'0') + '-' +
        String(cur.getDate()).padStart(2,'0');
      (map[key] = map[key] || []).push(ev.category);
      cur.setDate(cur.getDate()+1);
    }
  });
  return map;
}

function renderCalendar() {
  const L = T();
  const evDates = getEventDatesMap();
  const firstDay = new Date(calYear, calMonth, 1);
  const daysInMonth = new Date(calYear, calMonth+1, 0).getDate();
  const startDow = (firstDay.getDay()+6)%7;

  document.getElementById('cal-title').textContent = L.months[calMonth] + ' ' + calYear;

  const today = new Date(); today.setHours(0,0,0,0);
  let html = L.days.map(d => `<div class="cal-dh">${d}</div>`).join('');
  for (let i = 0; i < startDow; i++) html += '<div class="cal-day empty"></div>';

  for (let d = 1; d <= daysInMonth; d++) {
    const mm = String(calMonth+1).padStart(2,'0'), dd = String(d).padStart(2,'0');
    const dateStr = `${calYear}-${mm}-${dd}`;
    const dt = new Date(calYear, calMonth, d);
    const hasEv  = !!evDates[dateStr];
    const isToday = dt.toDateString() === today.toDateString();
    const isPast  = dt < today;
    const isSel   = dateStr === selectedDate;
    const cats = evDates[dateStr] || [];
    const dotColor = cats.length ? (COLORS[cats[0]] || '#c9a84c') : '#c9a84c';
    const cls = ['cal-day', hasEv?'has-ev':'', isToday?'today':'', isSel?'selected':'',
                 (isPast&&!isToday)?'past':''].filter(Boolean).join(' ');
    const canClick = !isPast || isToday;  // every current/future day is clickable
    const dot = hasEv ? `<span class="cal-dot" style="background:${isSel?'#000':dotColor}"></span>` : '';
    html += `<div class="${cls}"${canClick?` onclick="selectDate('${dateStr}')" title="${hasEv?cats.length+' event(s)':'No events'}"`:''} >${d}${dot}</div>`;
  }
  document.getElementById('cal-grid').innerHTML = html;

  const clearBtn = document.getElementById('cal-clear');
  clearBtn.style.display = selectedDate ? 'block' : 'none';
  clearBtn.textContent = L.showAll;

  // Legend
  const catCounts = {};
  EVENTS.forEach(ev => catCounts[ev.category] = (catCounts[ev.category]||0)+1);
  const legHtml = Object.entries(catCounts).sort((a,b)=>b[1]-a[1])
    .map(([cat,cnt]) =>
      `<div class="leg-item" onclick="filterCat('${cat}')">
        <span class="leg-dot" style="background:${COLORS[cat]||'#7f8c8d'}"></span>
        <span>${ICONS[cat]||'⭐'} ${esc(catLabel(cat))}</span>
        <span style="margin-left:auto;opacity:.55;font-size:.72rem">${cnt}</span>
      </div>`).join('');
  document.getElementById('cal-legend').innerHTML =
    `<div class="cal-legend-title">${L.categories}</div>${legHtml}`;
}

function navMonth(dir) {
  calMonth += dir;
  if (calMonth > 11) { calMonth=0; calYear++; }
  if (calMonth < 0)  { calMonth=11; calYear--; }
  renderCalendar();
}

function selectDate(date) {
  selectedDate = date;
  if (date) { const [y,m] = date.split('-').map(Number); calYear=y; calMonth=m-1; }
  renderCalendar();
  renderEvents();
}

document.getElementById('cal-prev').onclick = () => navMonth(-1);
document.getElementById('cal-next').onclick = () => navMonth(1);

// ── Filters ─────────────────────────────────────────────────

function buildFilters() {
  const L = T();
  const bar = document.getElementById('fbar');
  bar.innerHTML = '';
  const counts = {};
  EVENTS.forEach(e => counts[e.category] = (counts[e.category]||0)+1);

  const all = document.createElement('button');
  all.className = 'fbtn' + (activeCat==='all' ? ' on' : '');
  all.dataset.cat = 'all';
  all.onclick = () => filterCat('all');
  if (activeCat==='all') all.style.cssText = 'background:#c9a84c;border-color:transparent;color:#000';
  all.innerHTML = L.all + ' <span class="fcnt">' + EVENTS.length + '</span>';
  bar.appendChild(all);

  Object.keys(counts).sort().forEach(cat => {
    const btn = document.createElement('button');
    btn.className = 'fbtn' + (activeCat===cat ? ' on' : '');
    btn.dataset.cat = cat;
    btn.onclick = () => filterCat(cat);
    if (activeCat===cat) btn.style.cssText = `background:${COLORS[cat]||'#c9a84c'};border-color:transparent;color:#000`;
    btn.innerHTML = `${ICONS[cat]||'⭐'} ${esc(catLabel(cat))} <span class="fcnt">${counts[cat]}</span>`;
    bar.appendChild(btn);
  });

  buildSubbar();
}

// Second-tier subcategory chips for the active category
function buildSubbar() {
  const L = T();
  const sb = document.getElementById('subbar');
  sb.innerHTML = '';
  const subs = (activeCat !== 'all') ? subcatsFor(activeCat) : [];
  if (!subs.length) { sb.classList.remove('show'); return; }
  sb.classList.add('show');

  const color = COLORS[activeCat] || '#c9a84c';
  const allBtn = document.createElement('button');
  allBtn.className = 'sbtn' + (activeSubcat==='' ? ' on' : '');
  if (activeSubcat==='') allBtn.style.cssText = `background:${color};color:#000`;
  allBtn.textContent = L.allIn + ' ' + catLabel(activeCat);
  allBtn.onclick = () => filterSubcat('');
  sb.appendChild(allBtn);

  subs.forEach(([key, cnt]) => {
    const b = document.createElement('button');
    b.className = 'sbtn' + (activeSubcat===key ? ' on' : '');
    if (activeSubcat===key) b.style.cssText = `background:${color};color:#000`;
    b.innerHTML = `${esc(subcatLabel(key))} <span class="fcnt">${cnt}</span>`;
    b.onclick = () => filterSubcat(key);
    sb.appendChild(b);
  });
}

function filterCat(cat) {
  activeCat = cat;
  activeSubcat = '';          // reset subcategory when top category changes
  buildFilters();
  renderHero();
  renderEvents();
}

function filterSubcat(key) {
  activeSubcat = key;
  buildSubbar();
  renderHero();
  renderEvents();
}

// ── Cards ────────────────────────────────────────────────────

function card(ev) {
  const color = COLORS[ev.category] || '#7f8c8d';
  const icon  = ICONS[ev.category]  || '⭐';
  const label = catLabel(ev.category);
  const date  = fmtWhen(ev);
  const time  = (ev.time && !(ev.end && ev.end > ev.date)) ? ' · ' + ev.time : '';
  const L = T();

  const imgHtml = ev.image
    ? `<img class="card-img" src="${esc(ev.image)}" alt="${esc(ev.title)}" loading="lazy"
         onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      + `<div class="card-ph" style="background:linear-gradient(135deg,${color}22,${color}44);display:none">${icon}</div>`
    : `<div class="card-ph" style="background:linear-gradient(135deg,${color}22,${color}44)">${icon}</div>`;

  const link = ev.url
    ? `<a class="card-lnk" href="${esc(ev.url)}" target="_blank" rel="noopener">${esc(L.moreInfo)}</a>` : '';

  const key = evKey(ev);
  const isSaved = saved.has(key);
  const hasDate = ev.date && ev.date !== '9999-99-99';
  const actions = `<div class="card-actions">
    <button class="card-act${isSaved?' saved':''}" title="${esc(L.save)}"
      onclick="toggleSave('${esc(key).replace(/'/g,"\\'")}', this)">${isSaved?'♥':'♡'}</button>
    ${hasDate ? `<button class="card-act" title="${esc(L.addCal)}"
      onclick="addToCalendar('${esc(key).replace(/'/g,"\\'")}')">📅</button>` : ''}
  </div>`;

  return `<article class="card">
    ${imgHtml}
    <div class="card-body">
      <div class="card-meta">
        <span class="badge" style="background:${color}">${icon} ${esc(label)}</span>
        <span class="card-date">${esc(date)}${esc(time)}</span>
      </div>
      <h3 class="card-title">${esc(ev.title)}</h3>
      ${ev.venue ? `<div class="card-venue">📍 ${esc(ev.venue)}</div>` : ''}
      ${ev.description ? `<p class="card-desc">${esc(ev.description)}</p>` : ''}
      <div class="card-foot">
        ${actions}
        ${link}
      </div>
    </div>
  </article>`;
}

// ── Events render ────────────────────────────────────────────

const SECTION_KEYS = ['today','tomorrow','thisWeek','nextWeek','thisMonth','comingUp','noDate'];

function renderEvents() {
  const L = T();
  const area = document.getElementById('events-area');
  let list = activeCat==='all' ? EVENTS : EVENTS.filter(e=>e.category===activeCat);
  if (activeSubcat) list = list.filter(e=>e.subcat===activeSubcat);
  if (selectedDate) list = list.filter(e=>eventOnDay(e, selectedDate));
  if (showSaved)    list = list.filter(e=>saved.has(evKey(e)));
  if (searchQuery) {
    const q = searchQuery;
    list = list.filter(e =>
      (e.title||'').toLowerCase().includes(q) ||
      (e.venue||'').toLowerCase().includes(q) ||
      (e.description||'').toLowerCase().includes(q));
  }

  let html = '';
  const flat = selectedDate || showSaved || searchQuery;   // flat list vs date sections

  if (selectedDate) {
    html += `<div class="sel-header">
      <span>📅 ${esc(fmtDateLong(selectedDate))}</span>
      <span style="color:var(--muted);font-size:.85rem">${list.length}</span>
      <button onclick="selectDate(null)">${esc(L.showAll)}</button>
    </div>`;
  } else if (showSaved) {
    html += `<div class="sel-header">
      <span>♥ ${esc(L.savedTitle)}</span>
      <span style="color:var(--muted);font-size:.85rem">${list.length}</span>
      <button onclick="toggleSaved()">${esc(L.showAll)}</button>
    </div>`;
  } else if (searchQuery) {
    html += `<div class="sel-header">
      <span>🔍 "${esc(searchQuery)}"</span>
      <span style="color:var(--muted);font-size:.85rem">${list.length}</span>
      <button onclick="clearSearch()">${esc(L.showAll)}</button>
    </div>`;
  }

  if (!list.length) {
    const msg = showSaved ? L.noSaved : L.noEvents;
    area.innerHTML = html + `<div class="empty"><span>${showSaved?'♡':'🔍'}</span>${esc(msg)}</div>`;
    return;
  }

  if (flat) {
    html += `<div class="grid">${list.map(card).join('')}</div>`;
  } else {
    // "Recommended this week" row — only on the default, unfiltered view
    if (activeCat==='all' && !activeSubcat) {
      const rec = recommendedThisWeek();
      if (rec.length) {
        html += `<div class="sec-head sec-rec">⭐ ${esc(L.recommended)}</div>
                 <div class="grid">${rec.map(card).join('')}</div>`;
      }
    }
    const groups = {};
    list.forEach(ev => {
      const k = weekBucket(ev.date);
      (groups[k] = groups[k]||[]).push(ev);
    });
    SECTION_KEYS.forEach(key => {
      if (!groups[key]) return;
      html += `<div class="sec-head">${esc(L[key])}</div>
               <div class="grid">${groups[key].map(card).join('')}</div>`;
    });
  }
  area.innerHTML = html;
}

// Recommended events happening within the next 7 days, ranked by venue following
function recommendedThisWeek() {
  const today = new Date(); today.setHours(0,0,0,0);
  const in7 = new Date(today); in7.setDate(today.getDate()+7);
  const s7 = in7.toISOString().slice(0,10);
  const t0 = today.toISOString().slice(0,10);
  return EVENTS
    .filter(e => e.recommended && e.date && e.date !== '9999-99-99')
    .filter(e => {
      const end = e.end && e.end >= e.date ? e.end : e.date;
      return e.date <= s7 && end >= t0;     // overlaps the next 7 days
    })
    .sort((a,b) => (b.popularity||0) - (a.popularity||0))
    .slice(0, 12);
}

// ── Init ─────────────────────────────────────────────────────

const now = new Date();
calYear  = now.getFullYear();
calMonth = now.getMonth();

// If no events fall in the current month, jump to the first month that has events
const _evMap = getEventDatesMap();
if (Object.keys(_evMap).length > 0) {
  const _first = Object.keys(_evMap).sort()[0];
  const [_fy, _fm] = _first.split('-').map(Number);
  if (_fy > calYear || (_fy === calYear && _fm - 1 > calMonth)) {
    calYear  = _fy;
    calMonth = _fm - 1;
  }
}

// Mark the active lang button before setLang runs
document.querySelectorAll('.lang-btn').forEach(b =>
  b.classList.toggle('active', b.dataset.lang === lang));

setLang(lang);
</script>
</body>
</html>"""


def mark_announced(events: list) -> list:
    """Track when each event first appeared. Returns the 'featured' subset
    (announced within the last 7 days, image-first) for the hero carousel."""
    today = datetime.now().date()
    today_s = today.strftime("%Y-%m-%d")

    try:
        seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        seen = {}
    first_run = not seen

    def key_for(ev):
        k = ev.get("url") or ev.get("title", "")
        return re.sub(r"\s+", " ", k).strip()[:300]

    live_keys = set()
    for ev in events:
        k = key_for(ev)
        live_keys.add(k)
        first_seen = seen.get(k)
        if not first_seen:
            first_seen = today_s
            seen[k] = first_seen
        ev["announced"] = first_seen
        try:
            days = (today - datetime.strptime(first_seen, "%Y-%m-%d").date()).days
        except ValueError:
            days = 0
        ev["_new"] = 0 <= days <= 7

    # Forget events we haven't seen in a long time (keeps the file small)
    seen = {k: v for k, v in seen.items() if k in live_keys or
            (today - datetime.strptime(v, "%Y-%m-%d").date()).days < 120}
    try:
        STATE_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=0),
                              encoding="utf-8")
    except Exception as e:
        log.warning("Could not write %s: %s", STATE_FILE.name, e)

    # Featured: announced this week, with an image, upcoming/ongoing. On the very
    # first run everything is "new", so prefer image-rich, soonest events.
    pool = [e for e in events if e.get("_new") and e.get("image")]
    pool.sort(key=lambda e: (e.get("_sort", "9999-99-99")))
    featured = pool[:8]
    if first_run:
        log.info("First run: no history yet — all %d events count as new", len(events))
    log.info("Featured (announced this week): %d", len(featured))
    return featured


def build_html(events: list, featured: list, updated: str) -> str:
    html = _HTML
    events_json = json.dumps(events, ensure_ascii=False).replace("</script>", "<\\/script>")
    featured_json = json.dumps(featured, ensure_ascii=False).replace("</script>", "<\\/script>")
    html = html.replace("__EVENTS_JSON__", events_json)
    html = html.replace("__FEATURED_JSON__", featured_json)
    html = html.replace("__COLORS_JSON__", json.dumps(_CAT_COLORS, ensure_ascii=False))
    html = html.replace("__ICONS_JSON__",  json.dumps(_CAT_ICONS,  ensure_ascii=False))
    html = html.replace("__UPDATED__", updated)
    return html


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Prague Events scraper starting ===")
    all_events = []

    scrapers = [
        ("GoOut.net",   scrape_goout),
        ("KudyZNudy",   scrape_kudyznudy),
        ("Eventbrite",  scrape_eventbrite),
    ]

    for name, fn in scrapers:
        log.info("Scraping %s...", name)
        try:
            evs = fn()
            all_events.extend(evs)
            log.info("  %s → %d raw events", name, len(evs))
        except Exception as e:
            log.error("%s scraper crashed: %s", name, e)

    events = process(all_events)
    featured = mark_announced(events)
    updated = datetime.now().strftime("%d. %m. %Y %H:%M")
    html = build_html(events, featured, updated)

    OUT_FILE.write_text(html, encoding="utf-8")
    log.info("Written %s (%d unique events)", OUT_FILE, len(events))
    log.info("Open: %s", OUT_FILE.as_uri())
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
