"""
A tiny, configurable mov-cli scraper.

Defaults to a safe, public mini-catalog based on Blender open movies and
Google demo MP4s (CC/legit streams). You can change the source without
code edits using scraper options (see README).

Tested against mov-cli v4.4.x.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import re
import json
import requests
from lxml import html

# ---- mov-cli types (light duck-typing import) -------------------------------
# We don't import internal modules by path here to keep the plugin resilient.
# mov-cli exposes these at runtime to your scraper methods via typed objects.
# We only rely on attributes we actually use.
try:
    # These names are stable in v4.4.x; scrape signature change noted in issue #255.
    # https://github.com/mov-cli/mov-cli/issues/255
    from mov_cli.types import (
        Media,
        MediaType,
        Single,
        Source,
        SearchResult,
        Metadata,
        EpisodeSelector,
    )
except Exception:
    # Fallback minimal shims for type checkers / local import without mov-cli.
    @dataclass
    class Source:
        url: str
        headers: Optional[Dict[str, str]] = None

    @dataclass
    class Single:
        """Represents a single-playable media (movie, single video)."""
        source: Source
        type: str = "single"

    Media = Single  # simple alias for this plugin
    MediaType = str

    @dataclass
    class SearchResult:
        title: str
        url: str
        year: Optional[int] = None
        extra: Optional[Dict[str, Any]] = None

    @dataclass
    class Metadata:
        query: str
        url: Optional[str] = None
        # You may see more fields in real mov-cli; we use what we need.

    EpisodeSelector = Any


# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fuzzy_match(needle: str, haystack: str) -> bool:
    needle = normalize(needle).lower()
    haystack = normalize(haystack).lower()
    return all(tok in haystack for tok in needle.split())


class OpenMoviesScraper:
    """
    A small, general-purpose scraper:
      • "Catalog" mode: fetch a JSON or simple HTML list of title->url
      • CSS/XPath mode: scrape any page with selectors to extract (title, url)

    Select which behavior to use via options:
      source = "blender-json" | "css" | "html-list"
      url = starting page or json url (for css/html-list)
      item_selector = CSS selector for link nodes (for css)
      title_selector = CSS (optional: fallback to link text)
      href_attr = "href" (default) or another attribute containing the URL
      headers = optional request headers (json string or dict)

    Defaults:
      - A public JSON gist with demo MP4s + Blender open movies mirrors.
    """

    name = "OpenMovies"
    id = "DEFAULT"

    # ---- Search -------------------------------------------------------------
    def search(self, query: str, **kwargs) -> List[SearchResult]:
        # Pull options from mov-cli (scraper override config)
        opts = kwargs.get("options") or {}
        source_mode = (opts.get("source") or "blender-json").lower()

        items: List[Dict[str, str]] = []

        if source_mode == "blender-json":
            # Tiny, stable demo JSON (Google’s sample MP4s incl. Big Buck Bunny, Sintel, Tears of Steel)
            # https://gist.github.com/jsturgis/3b19447b304616f18657
            url = opts.get("url") or "https://gist.githubusercontent.com/jsturgis/3b19447b304616f18657/raw"
            headers = _load_headers(opts.get("headers"))
            data = _http_get_json(url, headers=headers) or []
            for entry in data:
                title = entry.get("title") or ""
                sources = entry.get("sources") or []
                if not title or not sources:
                    continue
                items.append({"title": title, "url": str(sources[0])})

        elif source_mode == "html-list":
            # Expect a simple HTML page with <a> links to MP4s or pages
            url = opts.get("url")
            if not url:
                raise RuntimeError("html-list mode requires options.url")
            headers = _load_headers(opts.get("headers"))
            doc = _http_get_html(url, headers=headers)
            for a in doc.xpath("//a[@href]"):
                title = normalize(a.text_content())
                href = a.get("href")
                if title and href and href.lower().endswith((".mp4", ".m3u8", ".webm")):
                    items.append({"title": title, "url": _absolutize(url, href)})

        else:  # "css"
            url = opts.get("url")
            item_selector = opts.get("item_selector")
            href_attr = opts.get("href_attr") or "href"
            title_selector = opts.get("title_selector")
            if not (url and item_selector):
                raise RuntimeError("css mode requires options.url and options.item_selector")
            headers = _load_headers(opts.get("headers"))
            doc = _http_get_html(url, headers=headers)
            for node in doc.cssselect(item_selector):
                # node is usually an <a>; otherwise pull child
                href = node.get(href_attr) or node.get("href")
                if not href:
                    link = node.cssselect("a[href]")
                    if link:
                        href = link[0].get("href")
                if not href:
                    continue
                title = ""
                if title_selector:
                    tnode = node.cssselect(title_selector)
                    if tnode:
                        title = normalize(tnode[0].text_content())
                if not title:
                    title = normalize(node.text_content())
                if not title:
                    title = href
                items.append({"title": title, "url": _absolutize(url, href)})

        # Filter by query fuzzily
        results = []
        for it in items:
            if not query or fuzzy_match(query, it["title"]):
                results.append(SearchResult(title=it["title"], url=it["url"]))
        # If nothing matches, return a few top items so the user can still pick
        if not results:
            results = [SearchResult(title=it["title"], url=it["url"]) for it in items[:10]]
        return results

    # ---- Scrape (resolve to a stream) --------------------------------------
    def scrape(
        self,
        metadata: Metadata,
        episode: Optional[EpisodeSelector] = None,
        **kwargs,
    ) -> Media:
        """
        Resolve the chosen SearchResult/URL into a playable stream.

        For this minimalist plugin we treat everything as a Single.
        If you later target series pages, you'd implement scrape_episodes()
        and produce a Multi() object per mov-cli docs.
        """
        # A SearchResult’s URL is passed back via Metadata.url by mov-cli.
        url = (getattr(metadata, "url", None) or "").strip() or (getattr(metadata, "query", "")).strip()
        if not url:
            # As a fallback, do an inline search using the query string.
            results = self.search(getattr(metadata, "query", ""), **kwargs)
            if not results:
                raise RuntimeError("No results found.")
            url = results[0].url

        headers = {"User-Agent": USER_AGENT}
        # Some sites require a Referer to permit streaming—allow override.
        opts = kwargs.get("options") or {}
        headers.update(_load_headers(opts.get("headers")) or {})

        src = Source(url=url, headers=headers)
        return Single(source=src)  # mov-cli will stream this in your configured player

    # ---- Episodes (optional; stub for compatibility) -----------------------
    def scrape_episodes(self, metadata: Metadata, **kwargs):
        """Return seasons/episodes data if your target site has episodic content."""
        return None


# ---- scraper registry expected by mov-cli -----------------------------------

def get_scrapers() -> Dict[str, Any]:
    """
    mov-cli discovers scrapers by calling this function from your package.
    Keys are scraper names; 'DEFAULT' enables `-s openmovies` without suffix.
    """
    return {
        "DEFAULT": OpenMoviesScraper(),
    }


# ---- tiny helpers -----------------------------------------------------------

def _http_get_html(url: str, headers: Optional[Dict[str, str]] = None) -> html.HtmlElement:
    r = requests.get(url, headers=headers or {"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return html.fromstring(r.text)

def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Any:
    r = requests.get(url, headers=headers or {"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return json.loads(r.text)

def _load_headers(h: Any) -> Optional[Dict[str, str]]:
    if not h:
        return None
    if isinstance(h, dict):
        return {str(k): str(v) for k, v in h.items()}
    if isinstance(h, str):
        try:
            return json.loads(h)
        except Exception:
            return None
    return None

def _absolutize(base: str, href: str) -> str:
    # basic absolutizer; good enough for simple catalogs
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        # take scheme+host from base
        m = re.match(r"^(https?://[^/]+)", base)
        return (m.group(1) if m else base.rstrip("/")) + href
    if base.endswith("/"):
        return base + href
    return base.rsplit("/", 1)[0] + "/" + href
