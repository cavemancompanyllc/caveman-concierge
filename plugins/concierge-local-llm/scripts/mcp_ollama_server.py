#!/usr/bin/env python3
"""
MCP server exposing the user's local Ollama models as a tool.

Purpose: let the orchestrator hand off bounded, low-stakes text tasks to a
local model instead of spending Claude tokens on them - log/tool-output
summarization, boilerplate drafting, simple classification/extraction. Not
for anything requiring judgment the orchestrator should own (secrets review,
commit decisions, architecture, anything the user will rely on without a
human/Claude re-check).

Talks to Ollama's local REST API (default http://localhost:11434, override
via the OLLAMA_HOST env var) - no network calls leave the machine unless
Ollama itself is pointed elsewhere. Requires Ollama running with the
requested model already pulled (`ollama pull <model>`).

All model selection, fallback, and reasoning-model quirks live in
local_llm.py (ships alongside this file), shared with the scripts that call
Ollama in-process.
This file is only the MCP wrapper. Editing it requires restarting the IDE /
Claude Code session - MCP servers don't hot-reload.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_llm import TIERS, LocalLLMError, ask  # noqa: E402

mcp = FastMCP("local-llm")


@mcp.tool()
def local_llm(prompt: str, system: str = "", tier: str = "default", model: str = "") -> str:
    """Run a bounded, low-stakes text task on a local Ollama model.

    Use for: summarizing long logs/tool output before ingesting them,
    drafting boilerplate text, simple classification or extraction from
    structured data. Do not use for: secrets/security judgment, commit
    decisions, architecture choices, or anything the user will act on
    without review - those stay with the orchestrating Claude model.

    Pick the tier by task shape, not by how important it feels:
      fast    (qwen3:8b)      single-field extraction or yes/no only. Tested
                              to mislabel item states when summarizing a
                              list - never use it to condense multi-item output.
      default (gpt-oss:20b)   summaries, multi-item digests, JSON extraction.
                              Use this when unsure.
      deep    (gpt-oss:120b)  heavier drafting. ~45s cold start, then slow;
                              don't use in a loop.
      vision  (gemma3:27b)    only tier that can read images.
      code    (qwen3-coder:30b) code-flavored text.

    When a local model explains a result you already computed, tell it
    explicitly not to re-decide or re-rank - otherwise it silently contradicts
    the input.

    Args:
        prompt: The task/content for the local model to process.
        system: Optional system prompt to steer the local model's behavior.
        tier: Model class - fast, default, deep, vision, or code. Falls back
            to the best pulled model if the preferred one is missing.
        model: Explicit Ollama tag, overriding tier. Leave empty normally.
    """
    if not model and tier not in TIERS:
        return f"ERROR: unknown tier {tier!r}; use one of: {', '.join(TIERS)}"
    try:
        return ask(prompt, system=system, tier=tier, model=model or None)
    except LocalLLMError as exc:
        return f"ERROR: {exc}"


if __name__ == "__main__":
    mcp.run()
