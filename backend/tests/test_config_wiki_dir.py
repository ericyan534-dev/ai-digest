"""A leaked inline .env comment must not become a real directory path."""

from __future__ import annotations

from aidigest.config import Settings


def _settings(**kw: object) -> Settings:
    return Settings(**kw)  # type: ignore[arg-type]


def test_wiki_dir_ignores_a_leaked_env_comment() -> None:
    """`AIDIGEST_WIKI_DIR=   # set to a dir ...` parses to the COMMENT TEXT, which
    is truthy — the exporter would create a directory literally named that."""
    s = _settings(AIDIGEST_WIKI_DIR="# set to a dir (e.g. ./wiki) to export digests")
    assert s.wiki_dir == ""


def test_wiki_dir_keeps_a_real_path() -> None:
    assert _settings(AIDIGEST_WIKI_DIR="./wiki").wiki_dir == "./wiki"


def test_wiki_dir_strips_surrounding_whitespace() -> None:
    assert _settings(AIDIGEST_WIKI_DIR="  ./wiki  ").wiki_dir == "./wiki"
