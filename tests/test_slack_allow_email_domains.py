from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from agent_bridge.platforms.slack.adapter import SlackAdapter, SlackInfoCache
from agent_bridge.platforms.slack.config import SlackConfig


def _make_adapter(allow_email_domains: frozenset[str]) -> SlackAdapter:
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._config = SlackConfig(
        bot_token="xoxb-x",
        app_token="xapp-x",
        allow_email_domains=allow_email_domains,
    )
    adapter._name_cache = SlackInfoCache()
    return adapter


def _client_with_email(email: str | None) -> MagicMock:
    client = MagicMock()
    profile: dict[str, str] = {}
    if email is not None:
        profile["email"] = email
    client.users_info = AsyncMock(return_value={"user": {"profile": profile}})
    return client


def _client_api_error() -> MagicMock:
    client = MagicMock()
    client.users_info = AsyncMock(
        side_effect=SlackApiError("users_info failed", {"error": "user_not_found"})
    )
    return client


# --- config parsing ---


def test_from_env_parses_domains(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_ALLOW_EMAIL_DOMAINS", " Cresclab.com , foo.org ,, ")
    cfg = SlackConfig.from_env()
    assert cfg.allow_email_domains == frozenset({"cresclab.com", "foo.org"})


def test_from_env_empty_means_allow_all(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setenv("AGENT_BRIDGE_SLACK_ALLOW_EMAIL_DOMAINS", "")
    cfg = SlackConfig.from_env()
    assert cfg.allow_email_domains == frozenset()


# --- SlackInfoCache.resolve_email_domain ---


async def test_resolve_email_domain_returns_domain():
    cache = SlackInfoCache()
    client = _client_with_email("alice@cresclab.com")
    assert await cache.resolve_email_domain("U1", client) == "cresclab.com"


async def test_resolve_email_domain_lowercases():
    cache = SlackInfoCache()
    client = _client_with_email("Bob@CRESCLAB.COM")
    assert await cache.resolve_email_domain("U1", client) == "cresclab.com"


async def test_resolve_email_domain_no_email_returns_none():
    cache = SlackInfoCache()
    client = _client_with_email(None)
    assert await cache.resolve_email_domain("U1", client) is None


async def test_resolve_email_domain_api_error_returns_none():
    cache = SlackInfoCache()
    assert await cache.resolve_email_domain("U1", _client_api_error()) is None


async def test_resolve_email_domain_cached_after_first_call():
    cache = SlackInfoCache()
    client = _client_with_email("alice@cresclab.com")
    await cache.resolve_email_domain("U1", client)
    await cache.resolve_email_domain("U1", client)
    assert client.users_info.await_count == 1


async def test_resolve_email_domain_none_cached_no_retry():
    cache = SlackInfoCache()
    client = _client_api_error()
    await cache.resolve_email_domain("U1", client)
    await cache.resolve_email_domain("U1", client)
    assert client.users_info.await_count == 1


# --- _email_domain_allowed ---


async def test_empty_allowlist_allows_any_user_without_api_call():
    adapter = _make_adapter(frozenset())
    client = _client_with_email("external@other.com")
    assert await adapter._email_domain_allowed("U1", client) is True
    client.users_info.assert_not_awaited()


async def test_empty_user_id_allowed_without_api_call():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    client = _client_with_email("anyone@anything.com")
    assert await adapter._email_domain_allowed("", client) is True
    client.users_info.assert_not_awaited()


async def test_allowed_domain_returns_true():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    client = _client_with_email("alice@cresclab.com")
    assert await adapter._email_domain_allowed("U1", client) is True


async def test_disallowed_domain_returns_false():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    client = _client_with_email("eve@external.com")
    assert await adapter._email_domain_allowed("U1", client) is False


async def test_api_failure_fails_closed():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    assert await adapter._email_domain_allowed("U1", _client_api_error()) is False


async def test_no_email_on_profile_fails_closed():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    client = _client_with_email(None)
    assert await adapter._email_domain_allowed("U1", client) is False


# --- end-to-end: disallowed user silently dropped ---


async def test_disallowed_user_silent_drop():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    adapter._bridge = MagicMock()
    adapter._bridge.handle_message = MagicMock(
        side_effect=AssertionError("agent should not be invoked")
    )
    client = _client_with_email("eve@external.com")
    say = AsyncMock()
    event = {"channel": "C1", "user": "U1", "text": "hi", "ts": "1.0"}

    await adapter._process_message(event, say, client)

    say.assert_not_awaited()
    adapter._bridge.handle_message.assert_not_called()


async def test_allowed_user_proceeds_past_gate():
    adapter = _make_adapter(frozenset({"cresclab.com"}))
    adapter._get_state = MagicMock(side_effect=AssertionError("gate passed"))
    client = _client_with_email("alice@cresclab.com")
    # conversations_info needed for the channel gate that comes next
    client.conversations_info = AsyncMock(return_value={"channel": {}})
    say = AsyncMock()
    event = {"channel": "C1", "user": "U1", "text": "hi", "ts": "1.0"}

    try:
        await adapter._process_message(event, say, client)
    except AssertionError as e:
        assert "gate passed" in str(e)
