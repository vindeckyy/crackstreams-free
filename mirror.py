#!/usr/bin/env python3
"""Mirror crackstreams.mx into ./site for GitHub Pages, ad-free.

Fetches the homepage, league pages, and stream pages; rewrites internal
links; drops ad/tracking scripts. Sources stay whatever crackstreams.mx
serves at build time (schedules + stream links go stale daily).

Env: BASE (default "/") — URL base when the site is not at a domain root.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

ORIGIN = "https://crackstreams.mx"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
OUT = os.environ.get("OUT_DIR", "site")

LEAGUE_PAGES = [
    "nflstreams", "nbaregular66", "mlbwildcard", "nhlstreams",
    "mmastreams", "boxingcasino", "ncaa", "wwestreams",
    "cfl", "wnbastreams", "f1streams", "ncaab",
]
NAV = [
    ("nflstreams", "NFL"), ("nbaregular66", "NBA"),
    ("mlbwildcard", "MLB"), ("nhlstreams", "NHL"),
    ("mmastreams", "MMA"), ("boxingcasino", "Boxing"),
    ("ncaa", "NCAA"), ("wwestreams", "WWE"),
]
LEAGUE_NAMES = {
    "nflstreams": "NFL", "nbaregular66": "NBA", "mlbwildcard": "MLB",
    "nhlstreams": "NHL", "mmastreams": "MMA", "boxingcasino": "Boxing",
    "ncaa": "NCAA", "wwestreams": "WWE", "cfl": "CFL",
    "wnbastreams": "WNBA", "f1streams": "F1", "ncaab": "NCAAB",
}
# (pattern, replacement) applied per <script>…</script> to drop ad/tracker code.
DROP_SCRIPTS = [
    re.compile(r"aclib\.runPop"),
    re.compile(r"googletagmanager\.com"),
    re.compile(r"histats\.com"),
    re.compile(r"cloudflareinsights\.com"),
    re.compile(r"challenge-platform"),
    re.compile(r"window\.__CF\$cv\$params"),
    re.compile(r"window\['\w+'\]\s*="),   # obfuscated inline player script
]
ADMaven_RE = re.compile(r"<meta name='admaven-placement'[^>]*>", re.I)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
STREAM_HREF_RE = re.compile(r'href="/stream/([a-z0-9-]+)"')

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name} Streams - CrackStreams</title>
  <link rel="shortcut icon" href="{base}img/ican.png" type="image/png"/>
  <link rel="stylesheet" href="{base}assets/css/index.css">
  <link rel="stylesheet" href="{base}assets/css/watch.css">
</head>
<body>

<header class="main-toolbar">
  <div class="toolbar-inner">
    <div class="toolbar-left">
      <a href="{base}index.html" class="toolbar-logo">CrackStreams</a>
      <div class="toolbar-toggle" onclick="toggleMenu()">&#9776;</div>
    </div>
    <nav class="toolbar-links" id="toolbarMenu">
{nav_items}
    </nav>
  </div>
</header>

<div class="container">
  <div class="main">
    <h1><a href="{base}index.html">{name} Streams</a> and Schedule</h1>
    <center><p>&#128161; Live links are added 1 hour before each main event</p></center>
  </div>
</div>
<div class="container">
  <aside class="sidebar">
    <div class="league-article">
      <h2><b>How can I watch Free {name} Streams?</b></h2>
      <h4>On this page you can see a list of events. Everything is listed by date
      and time. Scroll down the page and choose what you want to see and click on
      it. A new page opens with the player. Pick a link, press play, and the
      stream loads in HD.</h4>
    </div>
  </aside>
</div>
{footer}
<script>
function toggleMenu() {{
  const menu = document.getElementById('toolbarMenu');
  menu.classList.toggle('show');
}}
</script>
</body>
</html>
"""

FOOTER = """<footer style="background:#0a0a0a; padding:20px 0; text-align:center; color:#aaa; font-size:0.9rem; margin-top:40px; border-top:1px solid #333;">
  <div style="max-width:900px; margin:auto; padding:0 20px;">
    <p>&copy; 2026 CrackStreams All rights reserved.</p>
    <p>
      <a href="{base}index.html" style="color:#aaa; text-decoration:none; margin:0 10px;">CrackStreams</a>
      <a href="{base}league/mmastreams.html" style="color:#aaa; text-decoration:none; margin:0 10px;">CrackStreams MMA</a>
      <a href="{base}league/boxingcasino.html" style="color:#aaa; text-decoration:none; margin:0 10px;">Boxing Streams</a>
      <a href="{base}league/nflstreams.html" style="color:#aaa; text-decoration:none; margin:0 10px;">CrackStreams NFL</a>
      <a href="{base}stream/nfl-redzone.html" style="color:#aaa; text-decoration:none; margin:0 10px;">crackstreams Redzone</a>
      <a href="{base}league/wwestreams.html" style="color:#aaa; text-decoration:none; margin:0 10px;">WWE Streams</a>
    </p>
  </div>
</footer>
"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


FAIL_STREAK = {"n": 0}
IMG_CACHE = {}  # absolute URL -> local path under site/ (or None on failure)

CT_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
}


def download_img(url, base):
    """Download a remote image into site/img/; returns its base-relative path."""
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    r = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-w", "\n%{content_type}",
         "-A", UA, "-e", ORIGIN + "/", url],
        capture_output=True,
    )
    idx = r.stdout.rfind(b"\n")
    if r.returncode != 0 or idx < 0:
        log("img download failed: %s" % url)
        return None
    ctype = r.stdout[idx + 1:].decode("ascii", "replace").split(";")[0].strip().lower()
    body = r.stdout[:idx]
    ext = CT_EXT.get(ctype)
    if ext is None:
        m = re.search(r"\.([a-z0-9]{2,4})(?:[?#]|$)", url, re.I)
        ext = ("." + m.group(1).lower()) if m else ".img"
    dest_rel = "img/%s%s" % (h, ext)
    dest = os.path.join(OUT, dest_rel)
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(body)
    return dest_rel


def localize_images(html, base):
    """Point <img src> at locally downloaded copies (Pages-safe hotlinking)."""
    def repl(m):
        url = m.group(2)
        if url not in IMG_CACHE:
            IMG_CACHE[url] = download_img(url, base)
        local = IMG_CACHE[url]
        if local is None:
            return m.group(0)
        return '%s%s%s"' % (m.group(1), base, local)

    return re.sub(r'(<img\b[^>]*\bsrc=")(https?://[^"]+)"', repl, html, flags=re.I)


def fetch(path, binary=False, retries=4):
    """curl a page with a browser UA; retries with backoff (origin 403-limits)."""
    for attempt in range(retries):
        if FAIL_STREAK["n"] >= 3:
            log("cooling down 60s after %d consecutive fetch failures"
                % FAIL_STREAK["n"])
            time.sleep(60)
            FAIL_STREAK["n"] = 0
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "30", "-w", "\n%{http_code}",
             "-A", UA, ORIGIN + path],
            capture_output=True,
        )
        idx = r.stdout.rfind(b"\n")
        code = r.stdout[idx + 1:].decode("ascii", "replace") if idx >= 0 else "?"
        body = r.stdout[:idx] if idx >= 0 else r.stdout
        if r.returncode == 0 and code == "200":
            FAIL_STREAK["n"] = 0
            if binary:
                return body
            return body.decode("utf-8", errors="replace")
        FAIL_STREAK["n"] += 1
        log("fetch %s failed (code=%s rc=%d), attempt %d/%d"
            % (path, code, r.returncode, attempt + 1, retries))
        time.sleep(3 * (attempt + 1))
    return None


def clean(html):
    """Remove ad/tracking meta and scripts, keep the toolbar toggle script."""
    html = ADMaven_RE.sub("", html)
    out, pos = [], 0
    while True:
        m = re.compile(r"<script\b[^>]*>", re.I).search(html, pos)
        if not m:
            out.append(html[pos:])
            break
        end = html.find("</script>", m.end())
        if end < 0:
            out.append(html[pos:])
            break
        body = html[m.end():end]
        tag = m.group(0)
        if any(rx.search(body) or (rx.search(tag)) for rx in DROP_SCRIPTS):
            out.append(html[pos:m.start()])
        else:
            out.append(html[pos:end + len("</script>")])
        pos = end + len("</script>")
    return "".join(out)


def rewrite_links(html, base):
    """Map upstream URLs to static mirror URLs.

    - exact root ("/" or "/index") -> index.html
    - root-relative href/src get `base` prefixed
    - /league/<slug> and /stream/<slug> gain .html ( Pages serves static files)
    - everything else (absolute, protocol-relative) passes through
    """
    root = base.rstrip("/")
    html = re.sub(r'href="/(?:index)?"',
                  lambda m: 'href="%sindex.html"' % base, html, flags=re.I)
    for attr in ("href", "src"):
        # skip protocol-relative URLs and values already prefixed with base
        neg = "(?!//)" + ("(?!%s)" % re.escape(root + "/") if root else "")
        html = re.sub(
            r'(%s=")%s(/[^"]*)"' % (attr, neg),
            lambda m: '%s%s%s"' % (m.group(1), root, m.group(2)),
            html,
            flags=re.I,
        )
    html = re.sub(r'href="%s(league|stream)/([a-z0-9-]+)"' % re.escape(base),
                  lambda m: 'href="%s%s/%s.html"' % (base, m.group(1), m.group(2)),
                  html)
    return html


def nav_html(base):
    rows = []
    for slug, label in NAV:
        rows.append('                       <a href="%sleague/%s.html">%s</a>'
                    % (base, slug, label))
    return "\n".join(rows)


def build_league(slug, base, footer):
    path = "/league/%s" % slug
    html = fetch(path)
    if html is None:
        log("MISSING league %s (fetch failed or 404) -> placeholder page" % slug)
        html = NOT_FOUND_HTML.format(
            name=LEAGUE_NAMES.get(slug, slug.upper()), base=base,
            nav_items=nav_html(base), footer=footer,
        )
        with open(site("league", slug + ".html"), "w", encoding="utf-8") as f:
            f.write(html)
        return []
    slugs = sorted(set(STREAM_HREF_RE.findall(html)))
    html = localize_images(rewrite_links(clean(html), base), base)
    with open(site("league", slug + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    log("league %-12s %2d streams" % (slug, len(slugs)))
    return slugs


def build_stream(slug, base):
    html = fetch("/stream/%s" % slug, retries=1)
    if html is None:
        html = fetch("/stream/%s" % slug)  # one patient retry pass later
    if html is None or "allStreams" not in html:
        log("MISSING stream  %s (fetch failed, 404, or no links yet)" % slug)
        return None
    html = localize_images(rewrite_links(clean(html), base), base)
    html = html.replace("&copy; 2025", "&copy; 2026")
    with open(site("stream", slug + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    m = TITLE_RE.search(html)
    return m.group(1).strip() if m else slug


def site(*parts):
    return os.path.join(OUT, *parts)


def main():
    base = os.environ.get("BASE", "/")
    if base != "/" and not base.endswith("/"):
        base += "/"

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(site("league"))
    os.makedirs(site("stream"))
    with open(site(".nojekyll"), "w") as f:
        f.write("")

    for name, dest, binary in [
            ("assets/css/index.css", site("assets", "css", "index.css"), False),
            ("assets/css/watch.css", site("assets", "css", "watch.css"), False),
            ("img/ican.png", site("img", "ican.png"), True)]:
        data = fetch("/" + name, binary=binary)
        if data is None:
            log("MISSING asset /%s" % name)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if binary:
            with open(dest, "wb") as f:
                f.write(data)
        else:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(data)

    footer = FOOTER.format(base=base)

    index = fetch("/index")
    if index is None:
        log("FATAL: could not fetch /index")
        return 1
    index = localize_images(rewrite_links(clean(index), base), base)
    with open(site("index.html"), "w", encoding="utf-8") as f:
        f.write(index)

    all_slugs = set()
    for slug in LEAGUE_PAGES:
        all_slugs.update(build_league(slug, base, footer))

    pages = []
    for i, slug in enumerate(sorted(all_slugs)):
        if i:
            time.sleep(0.7)
        title = build_stream(slug, base)
        if title is not None:
            pages.append((slug, title))
            log("stream %-50s ok (%d/%d)" % (slug, len(pages), len(all_slugs)))

    lines = ["# Stream pages built", "",
             "| Page | Title |", "|---|---|"]
    for slug, title in pages:
        lines.append("| stream/%s.html | %s |" % (slug, title.replace("|", "/")))
    with open("SITEMAP.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log("done: %d league pages, %d stream pages" % (len(LEAGUE_PAGES), len(pages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
