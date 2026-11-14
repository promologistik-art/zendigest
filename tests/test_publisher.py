from publisher import render

def test_html_escaping_and_source_link():
    text = render([{
        "title": "A < B", "category": "Кино", "summary": "x & y",
        "why": "важно", "url": "https://example.com/?a=1&b=2"
    }], "14.11.2026")
    assert "A &lt; B" in text
    assert "x &amp; y" in text
    assert "https://example.com/?a=1&amp;b=2" in text
