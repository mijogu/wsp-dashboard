"""
WP Maintenance Dashboard — Regression Checker (Layer 1).

Runs headless-browser checks against each site's homepage:
  - HTTP status code
  - JavaScript console errors
  - Broken resources (images, scripts, CSS returning 4xx/5xx)
  - Page load time (ms)
  - Screenshot capture (viewport-sized PNG)

Requires: pip install playwright && playwright install chromium
"""

import json
import os
import threading
import time
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from PIL import Image, ImageChops
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# Where screenshots are stored (set by init_regression)
_screenshot_dir = None

# Active run state — only one run at a time
_run_lock = threading.Lock()
_active_run = None  # dict with run progress, or None
_cancel_requested = False


def request_cancel():
    """Signal the running regression check to stop after the current site."""
    global _cancel_requested
    _cancel_requested = True


def init_regression(base_dir: str):
    """Initialize the screenshot directory. Call once at startup."""
    global _screenshot_dir
    _screenshot_dir = os.path.join(base_dir, "screenshots")
    os.makedirs(_screenshot_dir, exist_ok=True)


def get_screenshot_dir() -> str:
    return _screenshot_dir or ""


def is_available() -> bool:
    """Check if Playwright is installed and ready."""
    return PLAYWRIGHT_AVAILABLE


# Pixels with a per-channel difference below this value are treated as unchanged
# (avoids flagging sub-pixel antialiasing, JPEG rounding, etc.)
_DIFF_PIXEL_THRESHOLD = 16


def compute_pixel_diff(baseline_path: str, current_path: str, diff_path: str) -> float | None:
    """
    Compare two full-page screenshots using per-pixel difference.
    Returns diff_score (0.0 = identical, 100.0 = every pixel changed), or None on error.
    Saves a visualization to diff_path: changed pixels highlighted red on a greyscale
    version of the current screenshot.

    Uses Pillow's C-level ImageChops operations — no Python pixel loops, no numpy needed.
    """
    if not PILLOW_AVAILABLE:
        return None
    try:
        img_baseline = Image.open(baseline_path).convert("RGB")
        img_current = Image.open(current_path).convert("RGB")

        # Baseline is authoritative — resize current if dimensions differ
        if img_baseline.size != img_current.size:
            img_current = img_current.resize(img_baseline.size, Image.LANCZOS)

        width, height = img_baseline.size
        total_pixels = width * height

        # Absolute per-channel difference
        diff = ImageChops.difference(img_baseline, img_current)

        # Build a single-channel "max diff" image across R, G, B channels
        diff_r, diff_g, diff_b = diff.split()
        diff_max = ImageChops.lighter(
            ImageChops.lighter(diff_r.convert("RGB"), diff_g.convert("RGB")),
            diff_b.convert("RGB")
        ).split()[0]   # back to single-channel L

        # Count changed pixels using histogram (all in C, no Python loop)
        hist = diff_max.histogram()   # 256 buckets
        changed = sum(hist[_DIFF_PIXEL_THRESHOLD + 1:])
        diff_score = round((changed / total_pixels) * 100.0, 2)

        # Build diff visualization: greyscale current + red highlights for changed pixels
        mask = diff_max.point(lambda v: 255 if v > _DIFF_PIXEL_THRESHOLD else 0)
        grey_bg = img_current.convert("L").convert("RGB")
        red_overlay = Image.new("RGB", img_current.size, (210, 50, 50))
        diff_vis = Image.composite(red_overlay, grey_bg, mask)
        diff_vis.save(diff_path)

        return diff_score
    except Exception:
        return None


def get_active_run() -> dict | None:
    """Return the current in-progress run state, or None."""
    return _active_run


def check_single_site(page, url: str, timeout_ms: int = 30000) -> dict:
    """
    Run all Layer 1 checks on a single URL using an existing Playwright page.
    Returns a result dict.
    """
    result = {
        "http_status": None,
        "load_time_ms": None,
        "js_errors": [],
        "broken_resources": [],
        "screenshot_path": None,
        "error": None,
    }

    js_errors = []
    broken = []

    # Collect JS console errors
    def on_page_error(err):
        js_errors.append(str(err))

    # Collect failed network responses (4xx/5xx)
    def on_response(response):
        if response.status >= 400:
            # Only track page sub-resources, not the main doc (that goes in http_status)
            broken.append({
                "url": response.url,
                "status": response.status,
            })

    page.on("pageerror", on_page_error)
    page.on("response", on_response)

    start = time.time()
    try:
        response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        result["load_time_ms"] = int((time.time() - start) * 1000)
        result["http_status"] = response.status if response else None
    except Exception as e:
        result["load_time_ms"] = int((time.time() - start) * 1000)
        result["error"] = str(e)

    result["js_errors"] = js_errors
    result["broken_resources"] = broken

    # Scroll the full page to trigger lazy-loaded images, IntersectionObserver
    # callbacks, and scroll-triggered CSS animations, then wait for any
    # network requests they spawn to settle before screenshotting.
    try:
        page.evaluate("""
            async () => {
                const delay = ms => new Promise(r => setTimeout(r, ms));
                const scrollHeight = document.body.scrollHeight;
                const viewportHeight = window.innerHeight;
                let y = 0;
                while (y < scrollHeight) {
                    window.scrollTo(0, y);
                    y += viewportHeight;
                    await delay(200);
                }
                // Scroll back to top for a clean full-page capture
                window.scrollTo(0, 0);
                await delay(500);
            }
        """)
        # Wait for any lazy-load network requests to finish
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass  # best-effort; don't fail the whole check if scroll stalls

    # Take screenshot
    try:
        slug = url.replace("https://", "").replace("http://", "").rstrip("/")
        slug = slug.replace("/", "_").replace(":", "_")[:80]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{slug}_{ts}.png"
        filepath = os.path.join(_screenshot_dir, filename)
        page.screenshot(path=filepath, full_page=True)
        result["screenshot_path"] = filename
    except Exception as e:
        if result["error"]:
            result["error"] += f"; Screenshot failed: {e}"
        else:
            result["error"] = f"Screenshot failed: {e}"

    # Remove listeners to avoid accumulating across sites
    page.remove_listener("pageerror", on_page_error)
    page.remove_listener("response", on_response)

    return result


def _get_pages_for_site(site_url: str, site_id, site_configs: dict) -> list:
    """
    Return the list of URLs to test for a site.
    Falls back to [site_url] if no config or no test_pages set.
    """
    if not site_configs:
        return [site_url]
    cfg = site_configs.get(str(site_id), {})
    raw = cfg.get("test_pages", "[]")
    try:
        pages = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pages = []
    # Filter to non-empty strings; fall back to root URL
    pages = [p.strip() for p in (pages or []) if str(p).strip()]
    return pages if pages else [site_url]


def run_checks(sites: list, add_log_fn, save_result_fn, finish_run_fn,
               run_id: int, timeout_ms: int = 30000, site_configs: dict = None,
               baselines: dict = None):
    """
    Run regression checks on all sites. Intended to be called in a background thread.

    Args:
        sites: list of dicts with 'id', 'name', 'url'
        add_log_fn: function(source, level, message) for server logging
        save_result_fn: function(run_id, result_dict) to persist each result
        finish_run_fn: function(run_id, total, issues) called when all done
        run_id: DB run ID for this check batch
        timeout_ms: per-site navigation timeout
        site_configs: dict keyed by str(site_id) with per-site settings
    """
    global _active_run

    global _cancel_requested
    _cancel_requested = False  # reset at start of each run

    # Count total pages across all sites for accurate progress tracking
    total_pages = sum(
        len(_get_pages_for_site(
            s.get("url", ""), s.get("id"), site_configs
        )) for s in sites
    )
    total = total_pages
    issues_found = 0

    with _run_lock:
        _active_run = {
            "run_id": run_id,
            "status": "running",
            "total": total,
            "checked": 0,
            "issues_found": 0,
            "current_site": None,
            "started_at": datetime.now().isoformat(),
        }

    add_log_fn("Regression", "info", f"Starting regression check on {total} sites")

    try:
        with sync_playwright() as p:
            # Launch with stealth flags to bypass WAF bot-detection
            # (SiteGround, Cloudflare, Sucuri, etc.)
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="WSP-Dashboard/1.0 Regression-Monitor (+mgunn@blueblazeassociates.com)",
                extra_http_headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
                ignore_https_errors=True,
            )
            # Remove the navigator.webdriver flag that bot detectors look for
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                // Overwrite the chrome.runtime to look like a real browser
                window.chrome = { runtime: {} };
                // Fake plugins array
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                // Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
            """)
            page = context.new_page()

            for site in sites:
                site_id = site.get("id")
                site_name = site.get("name", "Unknown")
                site_url = site.get("url", "")

                if not site_url:
                    add_log_fn("Regression", "warn", f"  {site_name}: no URL, skipping")
                    continue

                # Ensure URL has protocol
                if not site_url.startswith("http"):
                    site_url = "https://" + site_url

                # Get the list of pages to test for this site
                pages_to_test = _get_pages_for_site(site_url, site_id, site_configs)

                for page_url in pages_to_test:
                    # Cancel check between pages too
                    if _cancel_requested:
                        add_log_fn("Regression", "warn",
                                   f"Regression check cancelled after {_active_run['checked']} pages")
                        _active_run["status"] = "cancelled"
                        finish_run_fn(run_id, _active_run["checked"], issues_found, "cancelled")
                        _active_run = None
                        browser.close()
                        return

                    # Show progress as "SiteName (path)" for multi-page sites
                    if len(pages_to_test) > 1:
                        path_part = page_url.replace(site_url.rstrip("/"), "") or "/"
                        _active_run["current_site"] = f"{site_name} ({path_part})"
                    else:
                        _active_run["current_site"] = site_name

                    try:
                        result = check_single_site(page, page_url, timeout_ms)
                    except Exception as e:
                        result = {
                            "http_status": None,
                            "load_time_ms": None,
                            "js_errors": [str(e)],
                            "broken_resources": [],
                            "screenshot_path": None,
                            "error": str(e),
                        }

                    # Determine if this page has issues
                    has_issues = bool(
                        (result["http_status"] and result["http_status"] >= 400)
                        or result["js_errors"]
                        or result["broken_resources"]
                        or result["error"]
                    )
                    if has_issues:
                        issues_found += 1

                    # Visual diff against baseline (if one exists for this site/page)
                    diff_score = None
                    diff_screenshot_path = None
                    if (result["screenshot_path"] and baselines
                            and _screenshot_dir):
                        site_baselines = baselines.get(str(site_id), {})
                        # Normalise URL for lookup: try exact match, then strip/add
                        # trailing slash so https://example.com and
                        # https://example.com/ are treated as the same page.
                        page_url_norm = page_url.rstrip("/")
                        baseline = (site_baselines.get(page_url)
                                    or site_baselines.get(page_url_norm)
                                    or site_baselines.get(page_url_norm + "/"))
                        if baseline and baseline.get("screenshot_path"):
                            baseline_file = os.path.join(
                                _screenshot_dir, baseline["screenshot_path"]
                            )
                            current_file = os.path.join(
                                _screenshot_dir, result["screenshot_path"]
                            )
                            if not os.path.exists(baseline_file):
                                add_log_fn("Regression", "warn",
                                           f"  Baseline screenshot missing for "
                                           f"{page_url}: {baseline['screenshot_path']}")
                            elif not PILLOW_AVAILABLE:
                                add_log_fn("Regression", "warn",
                                           "  Pillow not installed — visual diff disabled. "
                                           "Fix: pip install Pillow")
                            else:
                                diff_filename = f"diff_{result['screenshot_path']}"
                                diff_file = os.path.join(_screenshot_dir, diff_filename)
                                score = compute_pixel_diff(baseline_file, current_file, diff_file)
                                if score is not None:
                                    diff_score = score
                                    diff_screenshot_path = diff_filename
                                    add_log_fn("Regression", "info",
                                               f"  Visual diff {page_url}: "
                                               f"{score:.2f}% changed")
                                else:
                                    add_log_fn("Regression", "warn",
                                               f"  Visual diff failed for {page_url}")
                        elif site_baselines:
                            # Baselines exist for this site but not this exact page URL
                            add_log_fn("Regression", "warn",
                                       f"  No baseline for {page_url} — "
                                       f"existing keys: {list(site_baselines.keys())}")

                    # Build the full record
                    full_result = {
                        "run_id": run_id,
                        "site_id": site_id,
                        "site_name": site_name,
                        "site_url": site_url,
                        "page_url": page_url,
                        "http_status": result["http_status"],
                        "load_time_ms": result["load_time_ms"],
                        "js_errors": json.dumps(result["js_errors"]),
                        "broken_resources": json.dumps(result["broken_resources"]),
                        "screenshot_path": result["screenshot_path"],
                        "has_issues": 1 if has_issues else 0,
                        "error": result["error"],
                        "diff_score": diff_score,
                        "diff_screenshot_path": diff_screenshot_path,
                    }

                    save_result_fn(run_id, full_result)

                    # Update progress
                    _active_run["checked"] += 1

                    # Brief pause between page checks — avoids triggering WAF
                    # rate-limiting (SiteGround, Cloudflare, Wordfence, etc.)
                    time.sleep(2)
                    _active_run["issues_found"] = issues_found

                    # Log result
                    status_icon = "⚠️" if has_issues else "✓"
                    detail_parts = []
                    if result["http_status"]:
                        detail_parts.append(f"HTTP {result['http_status']}")
                    if result["load_time_ms"] is not None:
                        detail_parts.append(f"{result['load_time_ms']}ms")
                    if result["js_errors"]:
                        detail_parts.append(f"{len(result['js_errors'])} JS errors")
                    if result["broken_resources"]:
                        detail_parts.append(
                            f"{len(result['broken_resources'])} broken resources")
                    detail = ", ".join(detail_parts)

                    log_label = site_name
                    if len(pages_to_test) > 1:
                        log_label += f" → {page_url}"
                    add_log_fn("Regression",
                               "warn" if has_issues else "ok",
                               f"  {status_icon} {log_label}: {detail}")

            browser.close()

    except Exception as e:
        add_log_fn("Regression", "error", f"Regression check failed: {e}")
        _active_run["status"] = "failed"
        _active_run["error"] = str(e)
        finish_run_fn(run_id, _active_run["checked"], issues_found, "failed")
        _active_run = None
        return

    add_log_fn("Regression", "ok",
               f"Regression check complete — {total} sites, {issues_found} with issues")

    _active_run["status"] = "completed"
    finish_run_fn(run_id, total, issues_found, "completed")
    _active_run = None
