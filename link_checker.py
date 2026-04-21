"""
WP Maintenance Dashboard — Link Checker (standalone, no Playwright required).

For each site:
  1. Fetch all public page URLs from wp-sitemap.xml (falls back to test_pages / homepage)
  2. Download each page's HTML and extract all <a href> links
  3. Deduplicate links across all pages for the site
  4. Check each unique internal link with HEAD → GET fallback
  5. Record broken links (4xx / 5xx / connection errors)

External links are classified and stored but NOT checked by default.
Set CHECK_EXTERNAL = True to enable (future feature).

No new dependencies — uses only stdlib + requests (already present).
"""

import json
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests as http_requests

# ── Configuration ─────────────────────────────────────────────

# Whether to check external links (different domain). False = classify only.
CHECK_EXTERNAL = False

# Max pages to crawl per site from the sitemap
MAX_PAGES_PER_SITE = 500

# Concurrent workers for page fetching (crawl phase)
PAGE_FETCH_WORKERS = 5

# Concurrent workers for link checking (check phase)
LINK_CHECK_WORKERS = 20

# Timeouts
PAGE_FETCH_TIMEOUT = 15   # seconds per page HTML fetch
LINK_CHECK_TIMEOUT = 15   # seconds per link HEAD/GET

# XML namespace used in WordPress sitemaps
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Shared user-agent (matches regression.py)
_USER_AGENT = "WSP-Dashboard/1.0 LinkChecker (+mgunn@blueblazeassociates.com)"

# ── Active run state ──────────────────────────────────────────

_run_lock = threading.Lock()
_active_check: dict | None = None
_cancel_requested = False


def request_cancel():
    global _cancel_requested
    _cancel_requested = True


def get_active_check() -> dict | None:
    return _active_check


# ── Link extraction ───────────────────────────────────────────

class _LinkExtractor(HTMLParser):
    """Minimal HTMLParser subclass that collects all href values."""

    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


def _extract_links(base_url: str, html: str) -> list[str]:
    """
    Parse HTML and return deduplicated absolute URLs from <a href> tags.
    Skips mailto:, tel:, javascript:, and bare # anchors.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass

    seen: set[str] = set()
    result: list[str] = []
    for href in parser.hrefs:
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        # Strip fragments
        parsed = urlparse(absolute)
        clean = parsed._replace(fragment="").geturl()
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _is_same_domain(url: str, site_url: str) -> bool:
    """Return True if url is on the same registered domain as site_url."""
    try:
        site_host = urlparse(site_url).netloc.lower().lstrip("www.")
        link_host = urlparse(url).netloc.lower().lstrip("www.")
        return link_host == site_host or link_host.endswith("." + site_host)
    except Exception:
        return False


# ── Sitemap fetching ──────────────────────────────────────────

def _parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """
    Parse a sitemap or sitemap-index XML.
    Returns (page_urls, sub_sitemap_urls).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    ns = _SITEMAP_NS
    sub_sitemaps: list[str] = []
    pages: list[str] = []

    for sm in root.findall(f"{{{ns}}}sitemap"):
        loc = sm.find(f"{{{ns}}}loc")
        if loc is not None and loc.text:
            sub_sitemaps.append(loc.text.strip())

    for url in root.findall(f"{{{ns}}}url"):
        loc = url.find(f"{{{ns}}}loc")
        if loc is not None and loc.text:
            pages.append(loc.text.strip())

    return pages, sub_sitemaps


def fetch_sitemap_urls(site_url: str, session: http_requests.Session,
                       timeout: int = 15) -> list[str]:
    """
    Return all public page URLs from the site's sitemap.
    Tries wp-sitemap.xml (WP core), sitemap_index.xml (Yoast), sitemap.xml.
    Falls back to [site_url] if none found.
    Capped at MAX_PAGES_PER_SITE.
    """
    base = site_url.rstrip("/")
    candidates = [
        f"{base}/wp-sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap.xml",
    ]

    for sitemap_url in candidates:
        try:
            resp = session.get(sitemap_url, timeout=timeout)
            if resp.status_code != 200:
                continue
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type and "<html" in resp.text[:200].lower():
                # Got an HTML page (redirect to 404 page etc.) — not a sitemap
                continue

            pages, sub_sitemaps = _parse_sitemap_xml(resp.text)

            # Sitemap index → fetch each sub-sitemap (one level deep)
            if sub_sitemaps and not pages:
                for sub_url in sub_sitemaps:
                    if len(pages) >= MAX_PAGES_PER_SITE:
                        break
                    try:
                        sub_resp = session.get(sub_url, timeout=timeout)
                        if sub_resp.status_code == 200:
                            sub_pages, _ = _parse_sitemap_xml(sub_resp.text)
                            pages.extend(sub_pages)
                    except Exception:
                        pass

            if pages:
                return pages[:MAX_PAGES_PER_SITE]

        except Exception:
            continue

    # Nothing found — fall back to homepage
    return [site_url]


# ── Single-link HTTP check ────────────────────────────────────

def _check_link(url: str, session: http_requests.Session,
                timeout: int = LINK_CHECK_TIMEOUT) -> dict:
    """
    Check one URL with HEAD first, GET fallback on 405 / connection error.
    Returns dict with status_code, redirect_url, is_broken, error.
    """
    result = {
        "link_url": url,
        "status_code": None,
        "redirect_url": None,
        "is_broken": False,
        "error": None,
    }
    try:
        resp = session.head(
            url,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code == 405:
            raise http_requests.exceptions.InvalidURL("HEAD not allowed")
        result["status_code"] = resp.status_code
        if resp.url != url:
            result["redirect_url"] = resp.url
        result["is_broken"] = resp.status_code >= 400
    except (http_requests.exceptions.InvalidURL,
            http_requests.exceptions.ConnectionError,
            http_requests.exceptions.Timeout):
        # Retry with GET
        try:
            resp = session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,   # don't download body
            )
            resp.close()
            result["status_code"] = resp.status_code
            if resp.url != url:
                result["redirect_url"] = resp.url
            result["is_broken"] = resp.status_code >= 400
        except http_requests.exceptions.Timeout:
            result["is_broken"] = True
            result["error"] = "Timeout"
        except Exception as e:
            result["is_broken"] = True
            result["error"] = str(e)[:200]
    except Exception as e:
        result["is_broken"] = True
        result["error"] = str(e)[:200]

    return result


# ── Per-page HTML fetch ───────────────────────────────────────

def _fetch_page_links(page_url: str, site_url: str,
                      session: http_requests.Session) -> list[dict]:
    """
    Fetch one page, extract all links, classify internal vs external.
    Returns list of {link_url, source_page, is_external}.
    """
    try:
        resp = session.get(page_url, timeout=PAGE_FETCH_TIMEOUT)
        if resp.status_code >= 400:
            return []
        html = resp.text
    except Exception:
        return []

    raw_links = _extract_links(page_url, html)
    result = []
    for link in raw_links:
        parsed = urlparse(link)
        if not parsed.scheme.startswith("http"):
            continue
        is_ext = not _is_same_domain(link, site_url)
        result.append({
            "link_url": link,
            "source_page": page_url,
            "is_external": is_ext,
        })
    return result


def _get_pages_for_site(site_url: str, site_id, site_configs: dict | None,
                        session: http_requests.Session) -> list[str]:
    """
    Return pages to crawl. Uses sitemap first; falls back to configured
    test_pages (or homepage) if sitemap yields nothing useful.
    """
    pages = fetch_sitemap_urls(site_url, session)
    # If sitemap only returned the homepage fallback, also check test_pages config
    if len(pages) == 1 and pages[0] == site_url and site_configs:
        cfg = site_configs.get(str(site_id), {})
        raw = cfg.get("test_pages", "[]")
        try:
            configured = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            configured = []
        configured = [p.strip() for p in (configured or []) if str(p).strip()]
        if configured:
            return configured
    return pages


# ── Main entry point ──────────────────────────────────────────

def run_link_check(sites: list, add_log_fn, save_result_fn, finish_run_fn,
                   run_id: int, site_configs: dict | None = None):
    """
    Run link checks on all sites. Call in a background thread.

    Args:
        sites:          list of dicts with 'id', 'name', 'url'
        add_log_fn:     function(source, level, message)
        save_result_fn: function(run_id, result_dict)
        finish_run_fn:  function(run_id, pages, links, broken, status)
        run_id:         DB run ID
        site_configs:   dict keyed by str(site_id) with per-site settings
    """
    global _active_check, _cancel_requested
    _cancel_requested = False

    total_pages_crawled = 0
    total_links_checked = 0
    total_broken = 0

    with _run_lock:
        _active_check = {
            "run_id": run_id,
            "status": "running",
            "total_sites": len(sites),
            "checked_sites": 0,
            "current_site": None,
            "total_pages": 0,
            "total_links": 0,
            "broken_links": 0,
            "started_at": datetime.utcnow().isoformat(),
        }

    add_log_fn("LinkChecker", "info",
               f"Starting link check on {len(sites)} sites")

    session = http_requests.Session()
    session.headers.update({
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        for site_idx, site in enumerate(sites):
            if _cancel_requested:
                add_log_fn("LinkChecker", "warn",
                           f"Link check cancelled after {site_idx} sites")
                _active_check["status"] = "cancelled"
                finish_run_fn(run_id, total_pages_crawled,
                              total_links_checked, total_broken, "cancelled")
                _active_check = None
                session.close()
                return

            site_id = site.get("id")
            site_name = site.get("name", "Unknown")
            site_url = site.get("url", "")
            if not site_url:
                continue
            if not site_url.startswith("http"):
                site_url = "https://" + site_url

            _active_check["current_site"] = site_name

            add_log_fn("LinkChecker", "info", f"  {site_name}: fetching sitemap…")

            # Phase 1 — get pages to crawl
            pages_to_crawl = _get_pages_for_site(
                site_url, site_id, site_configs, session
            )
            add_log_fn("LinkChecker", "info",
                       f"  {site_name}: crawling {len(pages_to_crawl)} pages")

            # Phase 2 — crawl pages in parallel, collect links
            all_links: list[dict] = []
            with ThreadPoolExecutor(max_workers=PAGE_FETCH_WORKERS) as pool:
                futures = {
                    pool.submit(_fetch_page_links, pg, site_url, session): pg
                    for pg in pages_to_crawl
                }
                for fut in as_completed(futures):
                    if _cancel_requested:
                        break
                    try:
                        all_links.extend(fut.result())
                    except Exception:
                        pass

            total_pages_crawled += len(pages_to_crawl)

            # Deduplicate: keep one entry per unique (link_url, source_page) pair.
            # For the check we only need unique link_urls; track which source pages
            # surfaced each link so we can store that info on failure.
            seen_urls: dict[str, str] = {}   # link_url → first source_page
            for entry in all_links:
                lu = entry["link_url"]
                if lu not in seen_urls:
                    seen_urls[lu] = entry["source_page"]

            # Separate internal vs external; skip external unless CHECK_EXTERNAL
            internal_map: dict[str, str] = {}
            external_map: dict[str, str] = {}
            for entry in all_links:
                lu = entry["link_url"]
                if lu not in internal_map and lu not in external_map:
                    if entry["is_external"]:
                        external_map[lu] = entry["source_page"]
                    else:
                        internal_map[lu] = entry["source_page"]

            to_check = dict(internal_map)
            if CHECK_EXTERNAL:
                to_check.update(external_map)

            add_log_fn("LinkChecker", "info",
                       f"  {site_name}: {len(internal_map)} internal links, "
                       f"{len(external_map)} external — checking {len(to_check)}")

            # Phase 3 — check links concurrently
            site_broken = 0
            with ThreadPoolExecutor(max_workers=LINK_CHECK_WORKERS) as pool:
                futures = {
                    pool.submit(_check_link, url, session): (url, src)
                    for url, src in to_check.items()
                }
                for fut in as_completed(futures):
                    if _cancel_requested:
                        break
                    url, source_page = futures[fut]
                    try:
                        check = fut.result()
                    except Exception as e:
                        check = {
                            "link_url": url,
                            "status_code": None,
                            "redirect_url": None,
                            "is_broken": True,
                            "error": str(e)[:200],
                        }

                    total_links_checked += 1
                    _active_check["total_links"] = total_links_checked

                    if check["is_broken"]:
                        site_broken += 1
                        total_broken += 1
                        _active_check["broken_links"] = total_broken

                        is_ext = url in external_map
                        save_result_fn(run_id, {
                            "site_id": site_id,
                            "site_name": site_name,
                            "site_url": site_url,
                            "source_page": source_page,
                            "link_url": url,
                            "status_code": check["status_code"],
                            "redirect_url": check["redirect_url"],
                            "is_broken": True,
                            "is_external": is_ext,
                            "error": check.get("error"),
                        })

            _active_check["total_pages"] = total_pages_crawled
            _active_check["checked_sites"] = site_idx + 1

            add_log_fn("LinkChecker",
                       "warn" if site_broken else "ok",
                       f"  {'⚠️' if site_broken else '✓'} {site_name}: "
                       f"{len(pages_to_crawl)} pages, {len(to_check)} links, "
                       f"{site_broken} broken")

            # Brief pause between sites
            time.sleep(1)

    except Exception as e:
        add_log_fn("LinkChecker", "error", f"Link check failed: {e}")
        _active_check["status"] = "failed"
        finish_run_fn(run_id, total_pages_crawled,
                      total_links_checked, total_broken, "failed")
        _active_check = None
        session.close()
        return

    finally:
        session.close()

    add_log_fn("LinkChecker", "ok",
               f"Link check complete — {len(sites)} sites, "
               f"{total_pages_crawled} pages, {total_links_checked} links, "
               f"{total_broken} broken")

    _active_check["status"] = "completed"
    finish_run_fn(run_id, total_pages_crawled,
                  total_links_checked, total_broken, "completed")
    _active_check = None
