"""
Vercel Python serverless function: fetches the 6 tracked people's last-24h
activity from X (Nitter), YouTube (Atom), and one Substack RSS, renders the
same editorial HTML template the static morning snapshot uses, and returns it
with a 10-minute CDN cache + stale-while-revalidate.

Routed at /api/news. The static page's refresh button calls this and swaps
the response into the DOM in place.
"""
from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.request
import xml.etree.ElementTree as ET
import re
import html as htmllib
import zoneinfo


# ----- Config (mirrors ~/news-debrief/sources.json) ---------------------------

PEOPLE = [
    ("Andrej Karpathy", [
        ("x", "karpathy"),
        ("youtube", "UCYO_jab_esuFRV4b17AJtAw"),
    ]),
    ("Theo (t3.gg)", [
        ("youtube", "UCtuO2h6OwDueF7h3p8DYYjQ"),
        ("x", "theo"),
    ]),
    ("NetworkChuck", [
        ("youtube", "UCOuGATIAbd2DvzJmUgXn2IQ"),
        ("x", "NetworkChuck"),
    ]),
    ("Nate Herk", [
        ("youtube", "UC2ojq-nuP8ceeHqiroeKhBA"),
        ("x", "NateHerk"),
    ]),
    ("Elon Musk", [
        ("x", "elonmusk"),
    ]),
    ("Dean Ball", [
        ("blog", "https://www.hyperdimensional.co/feed"),
        ("x", "deanwball"),
    ]),
]

X_INSTANCES = ["https://nitter.net", "https://xcancel.com"]
FETCH_TIMEOUT = 6  # seconds per feed
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ----- Fetch ------------------------------------------------------------------

def http_get(url, timeout=FETCH_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _looks_like_feed(text):
    """Cheap check: a real RSS/Atom response starts with '<?xml' or '<rss' or '<feed' near the top."""
    head = text[:2048].lstrip()
    return ("<?xml" in head) or ("<rss" in head) or ("<feed" in head)


def _clean_xml(text):
    """Strip BOM and leading whitespace so ET.fromstring can parse."""
    if text and text[0] == "﻿":
        text = text[1:]
    return text.lstrip()


def fetch_feed(kind, key):
    """Returns (xml_text, error). On success error is None."""
    if kind == "x":
        last_err = "no instances tried"
        for base in X_INSTANCES:
            try:
                text = http_get(f"{base}/{key}/rss")
                text = _clean_xml(text)
                if not text:
                    last_err = f"{base}: empty response"
                    continue
                if not _looks_like_feed(text):
                    last_err = f"{base}: non-RSS response (account blocked or rate-limited)"
                    continue
                return text, None
            except Exception as e:
                last_err = f"{base}: {type(e).__name__}"
                continue
        return None, last_err
    if kind == "youtube":
        try:
            text = http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={key}")
            text = _clean_xml(text)
            if not _looks_like_feed(text):
                return None, "YouTube RSS endpoint returned non-feed (often 404)"
            return text, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
    if kind == "blog":
        try:
            text = http_get(key)
            text = _clean_xml(text)
            if not _looks_like_feed(text):
                return None, "non-RSS response"
            return text, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
    return None, f"unknown feed type {kind}"


# ----- Parse ------------------------------------------------------------------

def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_xml(text, kind, window_start, now_utc):
    try:
        root = ET.fromstring(text)
    except Exception as e:
        return None, f"parse error: {e}"
    items = []
    if kind in ("x", "blog"):
        for item in root.iter("item"):
            t = item.findtext("title") or ""
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate") or ""
            dt = parse_dt(pub)
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if window_start <= dt <= now_utc:
                items.append({"title": strip_tags(t), "link": link, "dt": dt})
    elif kind == "youtube":
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            t = entry.findtext("a:title", default="", namespaces=ns) or ""
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            pub = entry.findtext("a:published", default="", namespaces=ns) or ""
            dt = parse_dt(pub)
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if window_start <= dt <= now_utc:
                items.append({"title": strip_tags(t), "link": link, "dt": dt})
    return items, None


# ----- Render -----------------------------------------------------------------

CT = zoneinfo.ZoneInfo("America/Chicago")

PILL_LABEL = {"x": ("pill-x", "X"), "youtube": ("pill-yt", "YouTube"), "blog": ("pill-blog", "Blog")}

YT_SVG = ('<svg class="yt-logo" viewBox="0 0 28 20" aria-hidden="true" focusable="false">'
          '<rect width="28" height="20" rx="5.5" ry="5.5" fill="#FF0033"/>'
          '<polygon points="11.5,6 11.5,14 19,10" fill="#ffffff"/></svg>')

CSS = """
  :root {
    --bg: #F7F6F3;
    --surface: #FFFFFF;
    --ink: #2F3437;
    --muted: #787774;
    --border: #EAEAEA;
    --border-soft: rgba(17,17,17,0.06);
    --accent: #346538;
    --x-bg: #F1EFEC; --x-fg: #2F3437;
    --yt-bg: #FDEBEC; --yt-fg: #9F2F2D;
    --blog-bg: #FBF3DB; --blog-fg: #956400;
    --serif: ui-serif, "Iowan Old Style", "New York", Newsreader, "Lyon Text", Georgia, serif;
    --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", system-ui, sans-serif;
    --mono: ui-monospace, "SF Mono", "Geist Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { background: var(--bg); color: var(--ink); margin: 0; padding: 0; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
  body { font-family: var(--sans); font-size: 16px; line-height: 1.6; max-width: 720px; margin: 0 auto; padding: 96px 28px 96px; }
  .masthead { border-bottom: 1px solid var(--border); padding-bottom: 28px; margin-bottom: 56px; animation: rise 700ms cubic-bezier(0.16, 1, 0.3, 1) both; position: relative; }
  .eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin: 0 0 14px; font-variant-numeric: tabular-nums; }
  h1 { font-family: var(--serif); font-weight: 500; font-size: clamp(2.4rem, 6vw, 3.4rem); letter-spacing: -0.025em; line-height: 1.05; margin: 0; color: var(--ink); text-wrap: balance; }
  .sub { margin: 16px 0 0; color: var(--muted); font-size: 0.95rem; max-width: 56ch; }
  .refresh { position: absolute; top: 0; right: 0; font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; cursor: pointer; transition: color 200ms ease, border-color 200ms ease, transform 200ms ease; }
  .refresh:hover:not(:disabled) { color: var(--ink); border-color: #cfcfcf; transform: translateY(-1px); }
  .refresh:disabled { opacity: 0.6; cursor: wait; }
  .refresh .spin { display: inline-block; transition: transform 600ms ease; }
  .refresh.loading .spin { animation: spin 800ms linear infinite; }
  section { margin-top: 48px; animation: rise 700ms cubic-bezier(0.16, 1, 0.3, 1) both; animation-delay: calc((var(--i, 0) + 1) * 90ms); }
  section h2 { font-family: var(--sans); font-size: 1.02rem; font-weight: 600; letter-spacing: -0.005em; color: var(--ink); margin: 0 0 12px; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
  section h2 .count { font-family: var(--mono); font-weight: 400; font-size: 10.5px; letter-spacing: 0.1em; color: var(--muted); text-transform: uppercase; font-variant-numeric: tabular-nums; }
  ul { list-style: none; padding: 0; margin: 0; }
  li { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: baseline; padding: 10px 0; border-bottom: 1px solid var(--border-soft); line-height: 1.55; }
  li:last-child { border-bottom: none; }
  .pill { display: inline-block; font-family: var(--sans); font-size: 9.5px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 8px; border-radius: 9999px; white-space: nowrap; font-variant-numeric: tabular-nums; align-self: start; margin-top: 2px; }
  .pill-x { background: var(--x-bg); color: var(--x-fg); }
  .pill-yt { background: var(--yt-bg); color: var(--yt-fg); }
  .pill-blog { background: var(--blog-bg); color: var(--blog-fg); }
  .txt { color: var(--ink); }
  .ts { font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: 0.04em; font-variant-numeric: tabular-nums; margin-left: 8px; white-space: nowrap; opacity: 0.7; }
  .dest { font-family: var(--mono); font-size: 13px; color: var(--muted); text-decoration: none; padding: 2px 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); transition: color 200ms ease, border-color 200ms ease, transform 200ms ease; display: inline-flex; align-items: center; justify-content: center; min-width: 30px; height: 24px; line-height: 1; align-self: start; margin-top: 1px; }
  .dest:hover { color: var(--ink); border-color: #cfcfcf; transform: translateY(-1px); }
  .dest-yt { padding: 0; border: none; background: transparent; min-width: 0; height: auto; transition: transform 200ms ease, opacity 200ms ease; }
  .dest-yt:hover { transform: translateY(-1px); opacity: 0.9; border: none; }
  .yt-logo { display: block; width: 26px; height: auto; }
  .empty { font-style: italic; color: var(--muted); margin: 4px 0 0; font-size: 0.95rem; }
  .error { color: var(--muted); font-style: italic; font-size: 0.9rem; padding: 8px 0; }
  footer { margin-top: 88px; padding-top: 24px; border-top: 1px solid var(--border); font-family: var(--mono); font-size: 11.5px; color: var(--muted); letter-spacing: 0.03em; font-variant-numeric: tabular-nums; }
  footer .line2 { margin-top: 6px; opacity: 0.7; }
  @keyframes rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
  @media (max-width: 520px) { .refresh { position: static; margin-bottom: 16px; } }
""".strip()

REFRESH_JS = """
(function(){
  const btn = document.getElementById('refresh-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.classList.add('loading');
    btn.disabled = true;
    btn.querySelector('.label').textContent = 'Refreshing';
    try {
      const r = await fetch('/api/news?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const html = await r.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const newMain = doc.querySelector('main');
      const newH1 = doc.querySelector('h1');
      const newFooter = doc.querySelector('footer');
      if (newMain) document.querySelector('main').innerHTML = newMain.innerHTML;
      if (newH1) document.querySelector('h1').innerHTML = newH1.innerHTML;
      if (newFooter) document.querySelector('footer').innerHTML = newFooter.innerHTML;
      btn.querySelector('.label').textContent = 'Refresh';
    } catch (e) {
      btn.querySelector('.label').textContent = 'Failed';
      setTimeout(() => { btn.querySelector('.label').textContent = 'Refresh'; }, 2000);
    } finally {
      btn.classList.remove('loading');
      btn.disabled = false;
    }
  });
})();
""".strip()


def esc(s):
    return htmllib.escape(s or "", quote=True)


def shorten(text, max_len=240):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_len else text[:max_len].rsplit(" ", 1)[0] + "…"


def fmt_time(dt):
    h12 = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{h12}:{dt.minute:02d}:{dt.second:02d}{suffix}"


def part_of_day(h):
    if 5 <= h <= 11:
        return "this morning"
    if 12 <= h <= 16:
        return "this afternoon"
    if 17 <= h <= 20:
        return "this evening"
    if 21 <= h <= 23:
        return "tonight"
    return "overnight"


def relative(dt_utc, now_utc):
    delta = (now_utc - dt_utc).total_seconds()
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def absolute_ct(dt_utc):
    local = dt_utc.astimezone(CT)
    return local.strftime("%b ") + str(local.day) + ", " + fmt_time(local) + " CT"


def render_dest(label_key, href):
    if label_key == "youtube":
        return f'<a class="dest dest-yt" href="{esc(href)}" aria-label="Open on YouTube">{YT_SVG}</a>'
    sym, aria = {"x": ("𝕏", "Open on X"), "blog": ("✎", "Open blog post")}.get(label_key, ("↗", "Open link"))
    return f'<a class="dest" href="{esc(href)}" aria-label="{esc(aria)}">{sym}</a>'


def render(results, now_utc):
    now_ct = now_utc.astimezone(CT)
    date_str = now_ct.strftime("%A, %B ") + str(now_ct.day)
    footer_time = f"Last updated {part_of_day(now_ct.hour)} {fmt_time(now_ct)} CT"

    sections = []
    for idx, r in enumerate(results):
        items_html = []
        for it in r["items"]:
            kind = it["kind"]
            cls, label = PILL_LABEL.get(kind, ("pill-x", kind))
            line = shorten(it["title"])
            dt_utc = it["dt"].astimezone(timezone.utc) if it["dt"].tzinfo else it["dt"].replace(tzinfo=timezone.utc)
            time_el = (
                f'<time class="ts" datetime="{esc(dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))}" '
                f'title="{esc(absolute_ct(dt_utc))}">{esc(relative(dt_utc, now_utc))}</time>'
            )
            items_html.append(
                f'      <li><span class="pill {cls}">{esc(label)}</span>'
                f'<span class="txt">{esc(line)}{time_el}</span>'
                f'{render_dest(kind, it["link"])}</li>'
            )
        for kind, err in r["errors"]:
            _, label = PILL_LABEL.get(kind, ("pill-x", kind))
            items_html.append(
                f'      <li class="error" style="grid-column: 1/-1;">[fetch error in {esc(label)}: {esc(err)}]</li>'
            )

        count = len(r["items"])
        if items_html:
            body = (
                f'    <h2>{esc(r["name"])} <span class="count">{count} item{"s" if count != 1 else ""}</span></h2>\n'
                "    <ul>\n" + "\n".join(items_html) + "\n    </ul>"
            )
        else:
            body = f'    <h2>{esc(r["name"])}</h2>\n    <p class="empty">Nothing new in the last 24h.</p>'
        sections.append(f'  <section style="--i:{idx}">\n{body}\n  </section>')

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Daily Debrief — {esc(date_str)}</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <header class="masthead">
    <p class="eyebrow">Daily Debrief</p>
    <h1>{esc(date_str)}</h1>
    <p class="sub">Last 24h across six tracked feeds — short clips, offhand tweets, and posts you might otherwise miss.</p>
    <button id="refresh-btn" class="refresh" type="button" aria-label="Refresh now">
      <span class="spin">↻</span> <span class="label">Refresh</span>
    </button>
  </header>
  <main>
{chr(10).join(sections)}
  </main>
  <footer>
    <div>{esc(footer_time)}.</div>
    <div class="line2">Items shown: posts from the last 24 hours.</div>
  </footer>
  <script>
{REFRESH_JS}
  </script>
</body>
</html>
"""


# ----- Orchestrate ------------------------------------------------------------

def gather():
    """Fetch every (kind, key) in parallel, return dict (kind, key) -> (text, err)."""
    targets = []
    seen = set()
    for _, feeds in PEOPLE:
        for kind, key in feeds:
            if (kind, key) not in seen:
                seen.add((kind, key))
                targets.append((kind, key))

    results = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        future_to_target = {ex.submit(fetch_feed, k, v): (k, v) for k, v in targets}
        for fut in as_completed(future_to_target):
            target = future_to_target[fut]
            try:
                results[target] = fut.result()
            except Exception as e:
                results[target] = (None, f"{type(e).__name__}: {e}")
    return results


def assemble(now_utc, fetched):
    window_start = now_utc - timedelta(hours=24)
    out = []
    for name, feeds in PEOPLE:
        items, errors = [], []
        for kind, key in feeds:
            text, err = fetched.get((kind, key), (None, "missing"))
            if err is not None:
                errors.append((kind, err))
                continue
            parsed, perr = parse_xml(text, kind, window_start, now_utc)
            if perr is not None:
                errors.append((kind, perr))
            else:
                for it in parsed:
                    items.append({**it, "kind": kind})
        items.sort(key=lambda x: x["dt"], reverse=True)
        out.append({"name": name, "items": items, "errors": errors})
    return out


# ----- Vercel handler ---------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            now_utc = datetime.now(timezone.utc)
            fetched = gather()
            results = assemble(now_utc, fetched)
            html = render(results, now_utc)
            body = html.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=900")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            msg = f"news function error: {type(e).__name__}: {e}"
            body = msg.encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
