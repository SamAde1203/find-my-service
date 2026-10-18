"""Crawls a council website and splits its pages into searchable chunks.

Usage:
    fms-ingest https://localgov-council.ddev.site --out data/corpus.json

The crawler stays on one host, reads only the main content of each page,
and splits it into sections at each h2 heading. Sections are what the
assistant searches and cites, so a resident is sent to the right part of
the right page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from find_my_service.models import Chunk

# Paths that are not content: admin, login, search results, forms, files.
SKIP_PATHS = re.compile(
    r"^/(user|admin|search|node/\d+/|accessible-format/|bin-day|sites/default/files|core/)"
)
SKIP_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".png", ".svg", ".zip")
# Text that appears on every page and adds nothing to answers.
BOILERPLATE = re.compile(
    r"This file may not be suitable for users of assistive technology\.\s*"
    r"(Request an accessible format\s*\.?)?"
)
MIN_CHUNK_CHARS = 40
# Navigation, sidebars and forms inside <main> that are not page content.
NOT_CONTENT = ", ".join(
    [
        "script",
        "style",
        "nav",
        "form",
        ".visually-hidden",
        ".breadcrumb",
        ".lgd-region--sidebar-first",
        ".lgd-region--sidebar-second",
        ".lgd-prev-next",
        ".block-publication-navigation",
        ".block-localgov-guides-contents",
        # Teasers repeat the summaries of other pages, which are crawled anyway.
        ".lgd-teaser",
    ]
)


def normalise_url(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


def should_visit(url: str, host: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc != host:
        return False
    if parsed.query or parsed.path.lower().endswith(SKIP_EXTENSIONS):
        return False
    return not SKIP_PATHS.match(parsed.path)


def clean_text(text: str) -> str:
    text = BOILERPLATE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:)])", r"\1", text)


def extract_chunks(url: str, html: str) -> list[Chunk]:
    """Splits a page's main content into one chunk per h2 section."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        return []
    for tag in main.select(NOT_CONTENT):
        tag.decompose()

    h1 = main.find("h1")
    page_title = clean_text(h1.get_text(" ")) if isinstance(h1, Tag) else url

    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for element in main.find_all(["h2", "h3", "p", "li", "tr", "caption", "dt", "dd"]):
        if element.find_parent(["li", "tr"]) is not None:
            continue
        if element.name == "tr":
            text = _row_text(element)
        else:
            text = clean_text(element.get_text(" "))
            if element.name in ("li", "caption", "dd") and text:
                # List items become sentences, so answers can quote them on their own.
                text = text[0].upper() + text[1:]
                if text[-1] not in ".!?:":
                    text += "."
        if not text:
            continue
        if element.name == "h2":
            sections.append((text, []))
        else:
            sections[-1][1].append(text)

    # A LocalGov services landing page is a menu of other pages. Its summary
    # only repeats theirs, so keep just its own sections (such as contact details).
    if main.select_one(".service-landing-page") is not None:
        sections = [(h, p) for h, p in sections if h is not None]

    chunks = []
    for heading, parts in sections:
        body = " ".join(parts)
        if len(body) < MIN_CHUNK_CHARS:
            continue
        digest = hashlib.sha1(f"{url}#{heading}".encode()).hexdigest()[:10]  # noqa: S324
        chunks.append(Chunk(id=digest, url=url, page_title=page_title, heading=heading, text=body))
    return chunks


def _row_text(row: Tag) -> str:
    """Turns a table row into a sentence that makes sense on its own.

    "D | £2,280.18" becomes "Band: D, Annual charge: £2,280.18." using the
    column headers, so a search for "band D" finds the row and the answer
    can quote it. Header rows themselves are skipped.
    """
    cells = row.find_all(["th", "td"])
    if cells and all(c.name == "th" for c in cells):
        return ""
    table = row.find_parent("table")
    header_row = table.find("tr") if isinstance(table, Tag) else None
    headers = (
        [clean_text(h.get_text(" ")) for h in header_row.find_all("th")]
        if isinstance(header_row, Tag) and header_row is not row
        else []
    )
    values = [clean_text(c.get_text(" ")) for c in cells]
    if len(headers) == len(values):
        return ", ".join(f"{h}: {v}" for h, v in zip(headers, values, strict=True) if v) + "."
    return ", ".join(v for v in values if v) + "."


def crawl(
    start_url: str,
    max_pages: int = 200,
    client: httpx.Client | None = None,
    seeds: Iterable[str] = (),
) -> list[Chunk]:
    """Breadth-first crawl of one site, returning chunks from every page.

    `seeds` are extra paths to start from, for pages that nothing links to.
    """
    start_url = normalise_url(start_url)
    host = urlparse(start_url).netloc
    own_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True, verify=False)  # noqa: S501
    starts = [start_url, *(normalise_url(urljoin(start_url + "/", s.lstrip("/"))) for s in seeds)]
    seen = set(starts)
    queue: deque[str] = deque(starts)
    chunks: list[Chunk] = []
    try:
        while queue and len(seen) <= max_pages:
            url = queue.popleft()
            response = client.get(url)
            if response.status_code != 200 or "text/html" not in response.headers.get(
                "content-type", ""
            ):
                continue
            chunks.extend(extract_chunks(url, response.text))
            for link in _links(response.text, url):
                if link not in seen and should_visit(link, host):
                    seen.add(link)
                    queue.append(link)
    finally:
        if own_client:
            client.close()
    return dedupe(chunks)


def dedupe(chunks: list[Chunk]) -> list[Chunk]:
    """Drops repeated page summaries, such as a guide's lead on every guide page."""
    seen: set[str] = set()
    unique = []
    for chunk in chunks:
        key = chunk.text if chunk.heading is None else f"{chunk.url}#{chunk.heading}"
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique


def _links(html: str, base: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if isinstance(href, str):
            yield normalise_url(urljoin(base, href))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start_url", help="Home page of the council site to crawl")
    parser.add_argument("--out", type=Path, default=Path("data/corpus.json"))
    parser.add_argument(
        "--public-url",
        help="Replace the crawled host with this one in stored links, "
        "for example when crawling a local copy of a public site",
    )
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument(
        "--seed", action="append", default=[], help="Extra path to crawl from (repeatable)"
    )
    args = parser.parse_args(argv)

    chunks = crawl(args.start_url, args.max_pages, seeds=args.seed)
    if args.public_url:
        origin = normalise_url(args.start_url)
        public = normalise_url(args.public_url)
        for chunk in chunks:
            chunk.url = chunk.url.replace(origin, public, 1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps([c.model_dump() for c in chunks], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    pages = len({c.url for c in chunks})
    print(f"Saved {len(chunks)} chunks from {pages} pages to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
