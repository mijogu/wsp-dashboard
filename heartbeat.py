"""
WP Maintenance Dashboard — Heartbeat Scanner.

Checks per-site infrastructure and configuration stats that don't need
daily checking: DNS, SPF/DKIM/DMARC, SMTP reachability, robots.txt,
sitemap, staging, WP REST API, and RDAP/WHOIS.
"""

import json
import re
import smtplib
import socket
import threading
from datetime import datetime
from urllib.parse import urlparse

import requests as http_requests

try:
    import dns.resolver
    import dns.exception
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

_run_lock = threading.Lock()
_active_run = None
_cancel_requested = False

_DKIM_SELECTORS = ["default", "google", "k1", "selector1", "selector2", "mail"]
_USER_AGENT = "WSP-Dashboard/1.0 Heartbeat-Monitor (+mgunn@blueblazeassociates.com)"
_RDAP_BASE = "https://rdap.org/domain/{}"


def request_cancel():
    global _cancel_requested
    _cancel_requested = True


def get_active_run() -> dict | None:
    return _active_run


def is_running() -> bool:
    return _active_run is not None


def _apex_domain(url: str) -> str:
    """Extract apex domain from a site URL."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = parsed.hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _check_dns(domain: str) -> dict:
    """Resolve A, AAAA, MX, NS, TXT records. Returns {records: dict, error: str|None}."""
    if not DNS_AVAILABLE:
        return {"records": {}, "error": "dnspython not installed"}
    records = {}
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    for rtype in ("A", "AAAA", "MX", "NS", "TXT"):
        try:
            answers = resolver.resolve(domain, rtype, raise_on_no_answer=False)
            if answers.rrset:
                if rtype == "MX":
                    records[rtype] = [
                        {"priority": r.preference, "host": str(r.exchange).rstrip(".")}
                        for r in answers.rrset
                    ]
                elif rtype == "TXT":
                    records[rtype] = [
                        "".join(s.decode() for s in r.strings)
                        for r in answers.rrset
                    ]
                else:
                    records[rtype] = [str(r) for r in answers.rrset]
        except (dns.exception.DNSException, Exception):
            pass
    return {"records": records, "error": None}


def _check_spf(domain: str) -> tuple[str, str | None]:
    """Return (status, record) where status is 'pass'|'fail'|'unknown'."""
    if not DNS_AVAILABLE:
        return "unknown", None
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5
        answers = resolver.resolve(domain, "TXT", raise_on_no_answer=False)
        for r in (answers.rrset or []):
            txt = "".join(s.decode() for s in r.strings)
            if txt.startswith("v=spf1"):
                return "pass", txt
        return "fail", None
    except Exception:
        return "unknown", None


def _check_dkim(domain: str) -> tuple[str, str | None]:
    """Try common selectors. Return (status, selector_name)."""
    if not DNS_AVAILABLE:
        return "unknown", None
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    for sel in _DKIM_SELECTORS:
        try:
            host = f"{sel}._domainkey.{domain}"
            answers = resolver.resolve(host, "TXT", raise_on_no_answer=False)
            if answers.rrset:
                return "pass", sel
        except (dns.exception.NXDOMAIN, dns.exception.NoNameservers,
                dns.exception.Timeout, Exception):
            continue
    return "fail", None


def _check_dmarc(domain: str) -> tuple[str, str | None]:
    """Return (status, record)."""
    if not DNS_AVAILABLE:
        return "unknown", None
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5
        host = f"_dmarc.{domain}"
        answers = resolver.resolve(host, "TXT", raise_on_no_answer=False)
        for r in (answers.rrset or []):
            txt = "".join(s.decode() for s in r.strings)
            if txt.startswith("v=DMARC1"):
                return "pass", txt
        return "fail", None
    except Exception:
        return "unknown", None


def _check_smtp(domain: str) -> tuple[str, str]:
    """Check SMTP reachability via MX record. Return (status, detail)."""
    if not DNS_AVAILABLE:
        return "unknown", "dnspython not installed"
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5
        mx_answers = resolver.resolve(domain, "MX", raise_on_no_answer=False)
        mx_records = sorted(
            [(r.preference, str(r.exchange).rstrip(".")) for r in (mx_answers.rrset or [])],
            key=lambda x: x[0]
        )
        if not mx_records:
            return "fail", "No MX records found"
    except Exception:
        return "unknown", "MX lookup failed"

    host = mx_records[0][1]
    for port, use_ssl in [(25, False), (465, True)]:
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=5):
                    return "pass", f"{host}:{port} (SSL)"
            else:
                with smtplib.SMTP(host, port, timeout=5):
                    return "pass", f"{host}:{port}"
        except smtplib.SMTPException as e:
            return "fail", f"{host}:{port} SMTP error: {e}"
        except (ConnectionRefusedError, OSError):
            continue
        except Exception:
            continue
    return "unknown", f"{host} — ports 25/465 unreachable (may be ISP-blocked)"


def _check_robots(site_url: str) -> tuple[str, str | None, str | None]:
    """Return (status, version, content). status: 'bba'|'custom'|'none'|'unknown'."""
    url = site_url.rstrip("/") + "/robots.txt"
    try:
        r = http_requests.get(url, timeout=10, headers={"User-Agent": _USER_AGENT},
                              allow_redirects=True)
        if r.status_code == 404:
            return "none", None, None
        if r.status_code != 200:
            return "unknown", None, None
        content = r.text[:4000]
        m = re.search(r"#\s*---\s*Blue Blaze Robots\s+V(\d+)\s*---", content, re.IGNORECASE)
        if m:
            return "bba", f"V{m.group(1)}", content
        return "custom", None, content
    except Exception:
        return "unknown", None, None


def _check_sitemap(site_url: str) -> tuple[str, str | None]:
    """Return (status, url). status: 'pass'|'fail'."""
    base = site_url.rstrip("/")
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
        try:
            r = http_requests.get(base + path, timeout=10,
                                  headers={"User-Agent": _USER_AGENT},
                                  allow_redirects=True)
            if r.status_code == 200:
                return "pass", base + path
        except Exception:
            continue
    return "fail", None


def _check_staging(staging_url: str) -> tuple[str, str | None, str]:
    """Return (redirect_status, final_url, auth_status)."""
    if not staging_url:
        return "unknown", None, "unknown"
    try:
        r = http_requests.get(staging_url, timeout=10,
                              headers={"User-Agent": _USER_AGENT},
                              allow_redirects=True)
        final = r.url
        redirect_status = "ok" if r.status_code < 400 else "error"
    except Exception:
        return "unknown", None, "unknown"

    try:
        ra = http_requests.get(staging_url, timeout=10,
                               headers={"User-Agent": _USER_AGENT},
                               allow_redirects=False)
        auth_status = "pass" if ra.status_code == 401 else "fail"
    except Exception:
        auth_status = "unknown"

    return redirect_status, final, auth_status


def _check_wp_api(site_url: str) -> str:
    """Return 'pass'|'fail'|'unknown'."""
    url = site_url.rstrip("/") + "/wp-json/"
    try:
        r = http_requests.get(url, timeout=10, headers={"User-Agent": _USER_AGENT},
                              allow_redirects=True)
        if r.status_code == 200:
            return "pass"
        if r.status_code in (403, 404):
            return "fail"
        return "unknown"
    except Exception:
        return "unknown"


def _check_rdap(domain: str) -> dict:
    """Return RDAP info dict."""
    out = {
        "rdap_status": "unknown",
        "rdap_registrar": None,
        "rdap_expires_at": None,
        "rdap_created_at": None,
        "rdap_status_flags": None,
        "rdap_nameservers": None,
        "rdap_json": None,
    }
    try:
        r = http_requests.get(
            _RDAP_BASE.format(domain),
            timeout=15,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return out
        data = r.json()
        out["rdap_json"] = json.dumps(data)
        out["rdap_status"] = "ok"

        # Registrar
        for entity in data.get("entities", []):
            for role in entity.get("roles", []):
                if role == "registrar":
                    vcard = entity.get("vcardArray", [])
                    if len(vcard) > 1:
                        for prop in vcard[1]:
                            if prop[0] == "fn":
                                out["rdap_registrar"] = prop[3]
                                break
                    break

        # Dates
        for event in data.get("events", []):
            action = event.get("eventAction", "")
            date = event.get("eventDate", "")
            if action == "expiration":
                out["rdap_expires_at"] = date[:10] if date else None
            elif action == "registration":
                out["rdap_created_at"] = date[:10] if date else None

        # Status flags
        statuses = data.get("status", [])
        if statuses:
            out["rdap_status_flags"] = json.dumps(statuses)

        # Nameservers
        ns = [n.get("ldhName", "").rstrip(".").lower()
              for n in data.get("nameservers", []) if n.get("ldhName")]
        if ns:
            out["rdap_nameservers"] = json.dumps(ns)

    except Exception:
        pass
    return out


def check_site(site: dict, get_staging_url_fn=None) -> dict:
    """Run all heartbeat checks for a single site. Returns a result dict."""
    site_id = site.get("id")
    site_url = site.get("url", "")
    if not site_url:
        return {"site_id": site_id, "site_url": "", "error": "No URL"}

    if not site_url.startswith("http"):
        site_url = "https://" + site_url

    domain = _apex_domain(site_url)
    result = {"site_id": site_id, "site_url": site_url, "error": None}

    # DNS
    dns_info = _check_dns(domain)
    result["dns_json"] = json.dumps(dns_info["records"])

    # SPF
    result["spf_status"], result["spf_record"] = _check_spf(domain)

    # DKIM
    result["dkim_status"], result["dkim_selector"] = _check_dkim(domain)

    # DMARC
    result["dmarc_status"], result["dmarc_record"] = _check_dmarc(domain)

    # SMTP
    result["smtp_status"], result["smtp_detail"] = _check_smtp(domain)

    # robots.txt
    result["robots_status"], result["robots_version"], result["robots_content"] = \
        _check_robots(site_url)

    # Sitemap
    result["sitemap_status"], result["sitemap_url"] = _check_sitemap(site_url)

    # Staging (from Onboarding staging_url field)
    staging_url = get_staging_url_fn(site_id) if get_staging_url_fn else ""
    if staging_url:
        result["staging_status"], result["staging_final_url"], result["staging_auth_status"] = \
            _check_staging(staging_url)
    else:
        result["staging_status"] = "unknown"
        result["staging_final_url"] = None
        result["staging_auth_status"] = "unknown"

    # WP REST API
    result["wp_api_status"] = _check_wp_api(site_url)

    # RDAP
    rdap = _check_rdap(domain)
    result.update(rdap)

    return result


def run_heartbeat(sites: list, run_id: int, add_log_fn, save_result_fn,
                  finish_run_fn, get_staging_url_fn=None):
    """
    Run heartbeat checks on all sites. Call in a background thread.
    """
    global _active_run, _cancel_requested
    _cancel_requested = False

    total = len(sites)

    with _run_lock:
        _active_run = {
            "run_id": run_id,
            "status": "running",
            "total": total,
            "checked": 0,
            "current_site": None,
            "started_at": datetime.now().isoformat(),
        }

    add_log_fn("Heartbeat", "info", f"Starting heartbeat scan on {total} site(s)")

    try:
        for site in sites:
            if _cancel_requested:
                add_log_fn("Heartbeat", "warn",
                           f"Heartbeat cancelled after {_active_run['checked']} site(s)")
                _active_run["status"] = "cancelled"
                finish_run_fn(run_id, _active_run["checked"], "cancelled")
                _active_run = None
                return

            name = site.get("name", site.get("url", "unknown"))
            _active_run["current_site"] = name

            try:
                r = check_site(site, get_staging_url_fn)
            except Exception as e:
                r = {"site_id": site.get("id"), "site_url": site.get("url", ""),
                     "error": str(e)}

            save_result_fn(run_id, r)
            _active_run["checked"] += 1
            add_log_fn("Heartbeat", "ok", f"  ✓ {name}")

    except Exception as e:
        add_log_fn("Heartbeat", "error", f"Heartbeat scan failed: {e}")
        if _active_run:
            finish_run_fn(run_id, _active_run.get("checked", 0), "error")
        _active_run = None
        return

    add_log_fn("Heartbeat", "ok",
               f"Heartbeat scan complete — {total} site(s) checked")
    if _active_run:
        finish_run_fn(run_id, total, "completed")
    _active_run = None
