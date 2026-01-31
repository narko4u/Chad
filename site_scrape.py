import os, re, time, json
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.getenv("KB_SCRAPE_DIR", os.path.join(ROOT, "kb", "scraped"))
BASE_URL = os.getenv("KB_SCRAPE_BASE", "https://empirelabs.com.au").rstrip("/")
MAX_PAGES = int(os.getenv("KB_SCRAPE_MAX_PAGES", "60"))
TIMEOUT = int(os.getenv("KB_SCRAPE_TIMEOUT", "20"))
SLEEP_MS = int(os.getenv("KB_SCRAPE_SLEEP_MS", "250"))

# Only keep content from this host
BASE_HOST = urlparse(BASE_URL).netloc.lower()

# Skip common non-content routes
SKIP_PATTERNS = [
    r"\.png$", r"\.jpg$", r"\.jpeg$", r"\.gif$", r"\.webp$", r"\.svg$",
    r"\.pdf$", r"\.zip$", r"\.mp4$", r"\.mp3$", r"\.woff", r"\.woff2",
    r"/wp-admin", r"/admin", r"/login", r"/account"
]

HEADERS = {
    "User-Agent": "EmpireLabsKB-Scraper/1.0 (+local indexing)"
}

def should_skip(url: str) -> bool:
    u = url.lower()
    return any(re.search(p, u) for p in SKIP_PATTERNS)

def normalize_text(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content
    for tag in soup(["script","style","noscript","header","footer","nav","aside"]):
        tag.decompose()

    # Prefer <main> if present
    main = soup.find("main")
    root = main if main else soup.body if soup.body else soup

    text = root.get_text("\n")
    text = normalize_text(text)
    return text

def safe_filename(url: str) -> str:
    p = urlparse(url)
    path = p.path.strip("/")
    if not path:
        path = "home"
    path = re.sub(r"[^a-zA-Z0-9/_-]+", "-", path).strip("-")
    path = path.replace("/", "__")
    return f"{path}.md"

def get_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#"):
            continue
        u = urljoin(base_url, href)
        pu = urlparse(u)
        if pu.scheme not in ("http","https"):
            continue
        if pu.netloc.lower() != BASE_HOST:
            continue
        clean = pu._replace(fragment="").geturl()
        out.append(clean)
    return out

def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def write_doc(url: str, text: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    fn = safe_filename(url)
    fp = os.path.join(OUT_DIR, fn)
    front = f"---\nsource: {url}\nindexed_at: {int(time.time())}\n---\n\n"
    with open(fp, "w", encoding="utf-8") as f:
        f.write(front + text + "\n")
    return fp

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    seen = set()
    q = [BASE_URL]
    saved = []

    while q and len(seen) < MAX_PAGES:
        url = q.pop(0)
        if url in seen:
            continue
        if should_skip(url):
            continue
        seen.add(url)

        try:
            html = fetch(url)
            text = extract_main_text(html)
            if len(text) >= 200:  # ignore tiny pages
                fp = write_doc(url, text)
                saved.append(fp)
                print("saved:", fp)
            # enqueue links
            for u in get_links(html, url):
                if u not in seen and not should_skip(u):
                    q.append(u)
        except Exception as e:
            print("error:", url, str(e))

        time.sleep(SLEEP_MS/1000.0)

    # Write manifest
    man = os.path.join(OUT_DIR, "_manifest.json")
    with open(man, "w", encoding="utf-8") as f:
        json.dump({"base": BASE_URL, "count": len(saved), "files": saved}, f, indent=2)
    print("done. pages_saved:", len(saved), "manifest:", man)

if __name__ == "__main__":
    main()
