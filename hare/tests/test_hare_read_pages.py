"""Read tool `pages` param (PDF page range), aligned to 2.1.88."""

from hare.tools_impl.FileReadTool.file_read_tool import _parse_pages, input_schema


def test_schema_has_pages():
    props = input_schema()["properties"]
    assert "pages" in props and props["pages"]["type"] == "string"


def test_parse_single_page():
    assert _parse_pages("3", 10) == (2, 3)


def test_parse_range():
    assert _parse_pages("1-5", 10) == (0, 5)
    assert _parse_pages("10-20", 30) == (9, 20)


def test_parse_clamps_to_doc_and_cap():
    # end clamped to num_pages
    assert _parse_pages("1-100", 7) == (0, 7)
    # capped at 20 pages per read
    s, e = _parse_pages("1-50", 100)
    assert e - s == 20


def test_parse_open_ended():
    assert _parse_pages("5-", 12) == (4, 12)
