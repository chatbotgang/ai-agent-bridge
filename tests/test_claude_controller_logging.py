import asyncio
import logging
from pathlib import Path
from unittest.mock import patch

from agent_bridge.agents.claude.config import ClaudeConfig
from agent_bridge.agents.claude.controller import ClaudeController


def test_run_does_not_log_prompt(tmp_path: Path, caplog):
    secret = "xoxb-0000-secret-token"
    prompt = f'download with curl -H "Authorization: Bearer {secret}" https://example.com'
    controller = ClaudeController(ClaudeConfig(work_dir=tmp_path))

    async def consume() -> None:
        async for _ in controller.run("sid-1", prompt, True):
            pass

    # Abort right after the "Running claude" log line instead of spawning a real process.
    with (
        patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("stop")),
        caplog.at_level(logging.INFO, logger="agent_bridge.agents.claude.controller"),
    ):
        try:
            asyncio.run(consume())
        except RuntimeError:
            pass

    assert "Running claude" in caplog.text
    assert secret not in caplog.text
    assert "Bearer" not in caplog.text
    assert "sid-1" in caplog.text
