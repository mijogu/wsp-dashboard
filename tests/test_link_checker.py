"""
Tests for link_checker.py's HTML extraction — _extract_links, _parse_srcset,
and the scheme-allowlist fix. Pure functions, no network/DB involved.

Run with:  python -m unittest tests.test_link_checker -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from link_checker import _extract_links, _parse_srcset


class TestParseSrcset(unittest.TestCase):

    def test_single_candidate(self):
        self.assertEqual(_parse_srcset("/img.jpg 1x"), ["/img.jpg"])

    def test_multiple_candidates(self):
        value = "/img-320w.jpg 320w, /img-480w.jpg 480w, /img-800w.jpg 800w"
        self.assertEqual(
            _parse_srcset(value),
            ["/img-320w.jpg", "/img-480w.jpg", "/img-800w.jpg"],
        )

    def test_descriptor_discarded(self):
        # Only the URL (first token) should survive, not the "2x"/"320w" part
        urls = _parse_srcset("/a.jpg 2x, /b.jpg 3x")
        self.assertEqual(urls, ["/a.jpg", "/b.jpg"])

    def test_empty_candidates_skipped(self):
        self.assertEqual(_parse_srcset("/a.jpg 1x, , /b.jpg 2x"), ["/a.jpg", "/b.jpg"])

    def test_empty_string(self):
        self.assertEqual(_parse_srcset(""), [])


class TestExtractLinksSourceTags(unittest.TestCase):

    def _urls_by_tag(self, html, tag):
        entries = _extract_links("https://example.com/page", html)
        return sorted(e["url"] for e in entries if e["source_tag"] == tag)

    def test_anchor_links(self):
        html = '<a href="/page1">x</a><a href="/page2">y</a>'
        self.assertEqual(self._urls_by_tag(html, "a"),
                         ["https://example.com/page1", "https://example.com/page2"])

    def test_img_src(self):
        html = '<img src="/photo.jpg">'
        self.assertEqual(self._urls_by_tag(html, "img"), ["https://example.com/photo.jpg"])

    def test_img_srcset(self):
        html = '<img src="/base.jpg" srcset="/small.jpg 320w, /large.jpg 800w">'
        self.assertEqual(
            self._urls_by_tag(html, "img"),
            ["https://example.com/base.jpg", "https://example.com/large.jpg",
             "https://example.com/small.jpg"],
        )

    def test_source_srcset_tagged_as_img(self):
        html = '<picture><source srcset="/vid.webm 1x"><img src="/fallback.jpg"></picture>'
        self.assertEqual(
            self._urls_by_tag(html, "img"),
            ["https://example.com/fallback.jpg", "https://example.com/vid.webm"],
        )

    def test_link_stylesheet(self):
        html = '<link rel="stylesheet" href="/style.css">'
        self.assertEqual(self._urls_by_tag(html, "link"), ["https://example.com/style.css"])

    def test_link_non_stylesheet_ignored(self):
        # rel="icon"/"canonical"/etc. should NOT be picked up as a stylesheet
        html = ('<link rel="icon" href="/favicon.ico">'
                '<link rel="canonical" href="/canonical-page">')
        entries = _extract_links("https://example.com/page", html)
        self.assertEqual(entries, [])

    def test_script_src(self):
        html = '<script src="/app.js"></script>'
        self.assertEqual(self._urls_by_tag(html, "script"), ["https://example.com/app.js"])

    def test_iframe_src(self):
        html = '<iframe src="https://youtube.com/embed/xyz"></iframe>'
        self.assertEqual(self._urls_by_tag(html, "iframe"), ["https://youtube.com/embed/xyz"])


class TestExtractLinksSchemeAllowlist(unittest.TestCase):
    """The load-bearing correctness fix: only http/https URLs survive."""

    def test_data_uri_excluded(self):
        html = '<img src="data:image/png;base64,iVBORw0KGgoAAAANS">'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_blob_uri_excluded(self):
        html = '<script src="blob:https://example.com/abcd-1234"></script>'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_javascript_scheme_excluded(self):
        html = '<a href="javascript:void(0)">click</a>'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_mailto_excluded(self):
        html = '<a href="mailto:test@example.com">mail</a>'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_tel_excluded(self):
        html = '<a href="tel:+15551234567">call</a>'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_bare_fragment_excluded(self):
        html = '<a href="#section">jump</a>'
        self.assertEqual(_extract_links("https://example.com/page", html), [])

    def test_http_and_https_both_kept(self):
        html = '<a href="http://insecure.example.com">x</a><a href="https://secure.example.com">y</a>'
        urls = sorted(e["url"] for e in _extract_links("https://example.com/page", html))
        self.assertEqual(urls, ["http://insecure.example.com", "https://secure.example.com"])


class TestExtractLinksDedup(unittest.TestCase):

    def test_dedup_first_occurrence_wins(self):
        # Same URL referenced twice — once as a link, once as an image —
        # first occurrence's source_tag should win (accepted imprecision).
        html = '<a href="/thumb.jpg">view</a><img src="/thumb.jpg">'
        entries = _extract_links("https://example.com/page", html)
        matching = [e for e in entries if e["url"] == "https://example.com/thumb.jpg"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["source_tag"], "a")

    def test_fragment_stripped_dedups_with_bare_url(self):
        html = '<a href="/page">a</a><a href="/page#section">b</a>'
        entries = _extract_links("https://example.com/", html)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["url"], "https://example.com/page")


if __name__ == "__main__":
    unittest.main()
