import pytest
from dreamlake import encode_link_file, parse_link_file


def test_link_file_round_trip():
    for url in ("https://example.com/guide", "https://dreamlake.ai/example/memos/note?layout=8~m#heading"):
        text = encode_link_file(url)
        assert text == f"[InternetShortcut]\r\nURL={url}\r\n"
        assert parse_link_file(text) == url


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp/private", "https://u:p@example.com", "https://example.com/\nURL=https://other.test", "", "https://example.com:bad"])
def test_invalid_targets(url):
    with pytest.raises(ValueError):
        encode_link_file(url)


@pytest.mark.parametrize("content", ["URL=https://example.com", "[InternetShortcut]\nURL=https://a.test\nURL=https://b.test", "x" * 65537])
def test_invalid_files(content):
    with pytest.raises(ValueError):
        parse_link_file(content)
