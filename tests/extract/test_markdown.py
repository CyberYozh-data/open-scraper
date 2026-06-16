from __future__ import annotations

from src.extract.markdown import (
    convert_links_to_citations,
    html_to_markdown,
    only_main_content,
    prune_html,
)


class TestHtmlToMarkdown:
    def test_heading_and_paragraph(self):
        html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        md = html_to_markdown(html)
        assert "# Title" in md
        assert "Hello world" in md

    def test_unordered_list(self):
        html = "<ul><li>one</li><li>two</li></ul>"
        md = html_to_markdown(html)
        assert "  * one" in md or "* one" in md
        assert "two" in md

    def test_code_fence_not_indented(self):
        html = "<pre><code>print(1)</code></pre>"
        md = html_to_markdown(html)
        # The opening fence must start at column 0, not indented by html2text.
        assert "\n    ```" not in md
        assert "```" in md
        assert "print(1)" in md

    def test_ignore_links_keeps_anchor_text(self):
        html = '<p>See <a href="https://x.com">the site</a></p>'
        md = html_to_markdown(html, ignore_links=True)
        assert "the site" in md
        assert "https://x.com" not in md

    def test_ignore_images_drops_alt_markup(self):
        html = '<p><img src="https://x.com/a.png" alt="pic"></p>'
        md = html_to_markdown(html, ignore_images=True)
        assert "a.png" not in md

    def test_links_resolved_against_base_url(self):
        html = '<a href="/page">link</a>'
        md = html_to_markdown(html, base_url="https://x.com")
        assert "https://x.com/page" in md


class TestOnlyMainContent:
    SAMPLE = (
        "<html><body>"
        "<header>site header</header>"
        "<nav>menu links</nav>"
        "<main><h1>Article</h1><p>real content</p></main>"
        "<aside class='sidebar'>side stuff</aside>"
        '<div class="cookie">accept cookies</div>'
        "<footer>footer legal</footer>"
        "</body></html>"
    )

    def test_strips_boilerplate(self):
        cleaned = only_main_content(self.SAMPLE)
        assert "real content" in cleaned
        assert "site header" not in cleaned
        assert "menu links" not in cleaned
        assert "side stuff" not in cleaned
        assert "accept cookies" not in cleaned
        assert "footer legal" not in cleaned

    def test_unparseable_returns_input(self):
        # Must never raise; degrade gracefully.
        assert only_main_content("") == ""


class TestPruneHtml:
    ARTICLE = (
        "<p>This is a substantial paragraph of real article content that a "
        "reader actually cares about. It carries information, sentences, and "
        "meaning rather than a dense cluster of navigation links.</p>"
    )
    NAV = (
        "<nav>"
        '<a href="/a">Home</a><a href="/b">About</a><a href="/c">Shop</a>'
        '<a href="/d">Blog</a><a href="/e">Contact</a><a href="/f">Login</a>'
        "</nav>"
    )

    def test_keeps_content_drops_link_heavy_nav(self):
        html = f"<html><body><article>{self.ARTICLE}</article>{self.NAV}</body></html>"
        pruned = prune_html(html)
        assert "real article content" in pruned
        assert "About" not in pruned

    def test_unparseable_returns_input(self):
        assert prune_html("") == ""


class TestCitations:
    def test_replaces_inline_links_with_numbered_markers(self):
        md = "See [Home](https://x.com) and [About](https://x.com/about)."
        body, refs = convert_links_to_citations(md, base_url="https://x.com")
        assert "Home⟨1⟩" in body
        assert "About⟨2⟩" in body
        assert "(https://x.com)" not in body
        assert "⟨1⟩ https://x.com" in refs
        assert "⟨2⟩ https://x.com/about" in refs

    def test_same_url_shares_one_number(self):
        md = "[a](https://x.com) [b](https://x.com)"
        body, refs = convert_links_to_citations(md, base_url="https://x.com")
        assert "a⟨1⟩" in body and "b⟨1⟩" in body
        assert refs.count("https://x.com") == 1

    def test_relative_url_resolved_against_base(self):
        md = "[p](/page)"
        body, refs = convert_links_to_citations(md, base_url="https://x.com")
        assert "https://x.com/page" in refs

    def test_no_links_returns_empty_refs(self):
        body, refs = convert_links_to_citations("plain text", base_url="https://x.com")
        assert body == "plain text"
        assert refs == ""

    def test_images_are_not_turned_into_citations(self):
        md = "![pic](https://x.com/a.png) and [link](https://x.com)"
        body, refs = convert_links_to_citations(md, base_url="https://x.com")
        assert "![pic](https://x.com/a.png)" in body  # image untouched
        assert "link⟨1⟩" in body
        assert "a.png" not in refs


class TestExtractLinks:
    def test_absolutizes_and_dedups(self):
        from src.extract.markdown import extract_links

        html = (
            '<a href="/a">A</a><a href="https://y.com/b">B</a>'
            '<a href="/a">A dup</a><a>no href</a>'
        )
        links = extract_links(html, base_url="https://x.com")
        assert links == ["https://x.com/a", "https://y.com/b"]

    def test_empty_returns_empty_list(self):
        from src.extract.markdown import extract_links

        assert extract_links("", base_url="https://x.com") == []

    def test_drops_non_http_schemes(self):
        from src.extract.markdown import extract_links

        html = (
            '<a href="https://x.com/ok">ok</a>'
            '<a href="javascript:void(0)">js</a>'
            '<a href="mailto:a@b.com">mail</a>'
            '<a href="data:text/plain,hi">data</a>'
        )
        assert extract_links(html, base_url="https://x.com") == ["https://x.com/ok"]
