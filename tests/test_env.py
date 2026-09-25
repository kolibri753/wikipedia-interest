"""The stdlib .env loader: sets missing variables, never overrides real ones, ignores junk."""

import os

from wiki_interest.env import load_dotenv
from wiki_interest.http import JsonHttpClient, DEFAULT_USER_AGENT


def test_load_dotenv_sets_missing_and_respects_existing(tmp_path, monkeypatch=None):
    envfile = tmp_path / ".env"
    envfile.write_text(
        '# comment\nWIKI_INTEREST_TEST_A="quoted value"\nexport WIKI_INTEREST_TEST_B=plain\nWIKI_INTEREST_TEST_C=\nnot a line\n',
        encoding="utf-8",
    )
    os.environ.pop("WIKI_INTEREST_TEST_A", None)
    os.environ["WIKI_INTEREST_TEST_B"] = "already-set"
    try:
        assert load_dotenv(envfile) == 1
        assert os.environ["WIKI_INTEREST_TEST_A"] == "quoted value"
        assert os.environ["WIKI_INTEREST_TEST_B"] == "already-set"  # real environment wins
        assert "WIKI_INTEREST_TEST_C" not in os.environ  # empty values are ignored
        assert load_dotenv(tmp_path / "missing.env") == 0
    finally:
        os.environ.pop("WIKI_INTEREST_TEST_A", None)
        os.environ.pop("WIKI_INTEREST_TEST_B", None)


def test_user_agent_comes_from_environment_at_construction():
    os.environ.pop("WIKI_INTEREST_USER_AGENT", None)
    assert JsonHttpClient().user_agent == DEFAULT_USER_AGENT
    os.environ["WIKI_INTEREST_USER_AGENT"] = "my-skill/1.0 (mailto:me@example.org)"
    try:
        assert JsonHttpClient().user_agent.startswith("my-skill/1.0")
    finally:
        os.environ.pop("WIKI_INTEREST_USER_AGENT", None)
