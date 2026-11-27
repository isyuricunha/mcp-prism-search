import pytest

from mcp_prism_search.security import UnsafeUrlError, validate_url_shape
from mcp_prism_search.text import extract_html_text


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://metadata.google.internal",
    ],
)
def test_private_url_blocker_rejects_internal_targets(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url_shape(url)


def test_private_url_blocker_rejects_non_http_schemes() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url_shape("file:///etc/passwd")


def test_html_extraction_removes_script_style_nav_and_footer() -> None:
    html = """
    <html>
      <head>
        <title>Example Page</title>
        <style>.hidden { display: none; }</style>
        <script>window.secret = "do-not-include";</script>
      </head>
      <body>
        <nav>Navigation should be removed</nav>
        <main>
          <article class="article-content">
            This is the readable article body.
          </article>
        </main>
        <footer>Footer should be removed</footer>
      </body>
    </html>
    """

    extracted = extract_html_text(html, max_chars=5000)

    assert extracted.title == "Example Page"
    assert "This is the readable article body." in extracted.content
    assert "do-not-include" not in extracted.content
    assert "Navigation should be removed" not in extracted.content
    assert "Footer should be removed" not in extracted.content


def test_fetch_rejects_ambiguous_backslash_in_url_path():
    from mcp_prism_search.security import UnsafeUrlError, validate_url_shape

    with pytest.raises(UnsafeUrlError, match="backslashes"):
        validate_url_shape("https://example.com/path" + chr(92) + "segment")


def test_dns_resolution_order(monkeypatch):
    import socket

    from mcp_prism_search.security import _resolve_hostname

    def resolve(hostname, port, *, type):
        assert (hostname, port, type) == ("example.com", 443, socket.SOCK_STREAM)
        return [
            (socket.AF_INET6, 0, 0, "", ("2606:4700::1111", 443, 0, 0)),
            (socket.AF_INET, 0, 0, "", ("1.1.1.1", 443)),
            (socket.AF_INET, 0, 0, "", ("1.1.1.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert _resolve_hostname("example.com", 443) == ["1.1.1.1", "2606:4700::1111"]
