from backend.storage.article_titles import title_from_first_bytes


def test_title_parses_from_front_matter():
    md = """---
title: "Hello World"
summary: "x"
---

Body
"""
    assert title_from_first_bytes(md) == "Hello World"


def test_title_absent_returns_none():
    assert title_from_first_bytes("# No front matter") is None

