#!/usr/bin/env python3
"""Shared helper for handing bounded text work to a local Ollama model.

Purpose: keep Claude tokens for judgment and spend local compute on mechanical
text work instead - condensing long tool output before it's read, cleaning up
a scraped title, drafting boilerplate. Nothing here ever leaves the machine.

This is the in-process counterpart to scripts/mcp_ollama_server.py (which
exposes the same capability as an MCP tool to agent conversations). Scripts
can't call MCP tools, so they import this instead.

What NOT to use this for - same line the MCP server draws:
secrets/security judgment, commit decisions, architecture choices, or any
output acted on without a Claude/human re-check. In particular, never put a
local model in a path that takes an irreversible or unattended action; see
the rule-matching (no-LLM) design of `requests triage` in media_ctl.py.

Model tiers (see pick_model). Timings measured on an RTX 5090 (32GB) + 125GB
RAM; your mileage will differ, but the relative ordering holds:

    fast     qwen3:8b        ~2s warm. SINGLE-field extraction/classification
                             only (clean a title, yes/no). Reproducibly
                             mislabels item states when summarizing a list -
                             do not use it for condense().
    default  gpt-oss:20b     ~3-7s warm, fits fully in VRAM. The workhorse:
                             summarizing, multi-item digests, JSON extraction.
    deep     gpt-oss:120b    ~22 tok/s warm, but a ~45s COLD LOAD (65GB, split
                             across CPU/GPU). Occasional heavier drafting; too
                             slow to sit in a high-frequency path.
    vision   gemma3:27b      the only pulled model that can read images
    code     qwen3-coder:30b code-flavored text

First call to any model after Ollama starts pays its load time. Unattended
jobs should budget for that rather than treat a slow first reply as a hang.

Usage:
    from local_llm import ask, pick_model, LocalLLMError

    text = ask("Condense this to 5 bullets:\\n" + log, tier="fast")
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CHAT_URL = f"{OLLAMA_HOST}/api/chat"
TAGS_URL = f"{OLLAMA_HOST}/api/tags"
REQUEST_TIMEOUT = 180.0
# Models too big for VRAM load partly from disk into system RAM; the first
# call has to wait out that load before generating a single token.
LARGE_MODEL_TIMEOUT = 600.0
LARGE_MODEL_PREFIXES = ("gpt-oss:120b",)

TIERS = {
    "fast": "qwen3:8b",
    "default": "gpt-oss:20b",
    "deep": "gpt-oss:120b",
    "vision": "gemma3:27b",
    "code": "qwen3-coder:30b",
}

# Fallback chain per tier, tried in order when the preferred tag isn't pulled.
# Keeps a fresh clone of this repo working with whatever the user happens to
# have, instead of hard-failing on a model tag they've never heard of.
FALLBACKS = {
    "fast": ["qwen3:8b", "gpt-oss:20b", "gemma3:27b", "qwen3-coder:30b"],
    "default": ["gpt-oss:20b", "gemma3:27b", "qwen3-coder:30b", "qwen3:8b"],
    "deep": ["gpt-oss:120b", "gpt-oss:20b", "gemma3:27b", "qwen3-coder:30b"],
    "vision": ["gemma3:27b", "gpt-oss:20b"],
    "code": ["qwen3-coder:30b", "gpt-oss:20b", "gemma3:27b"],
}

# Reasoning models (qwen3, gpt-oss, deepseek-r1) wrap internal monologue in
# <think> tags. We ask Ollama to skip it via the "think" parameter, but older
# server builds ignore that, so strip any that comes back anyway - otherwise
# a caller parsing JSON out of the reply chokes on the preamble.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class LocalLLMError(RuntimeError):
    """Ollama unreachable, model missing, or the model returned nothing."""


def installed_models() -> list[str]:
    """Model tags currently pulled locally. Empty list if Ollama is down."""
    try:
        resp = httpx.get(TAGS_URL, timeout=10.0)
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL):
        return []
    return [m.get("name", "") for m in resp.json().get("models", [])]


def pick_model(tier: str = "default", available: list[str] | None = None) -> str:
    """Best pulled model for a tier, falling back down the chain.

    Raises LocalLLMError if nothing in the chain is pulled, with the pull
    command to fix it - a missing model is a setup gap, not a bug.
    """
    if tier not in TIERS:
        raise LocalLLMError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    if available is None:
        available = installed_models()
    if not available:
        raise LocalLLMError(
            f"no models reachable - is Ollama running at {OLLAMA_HOST}? "
            "Start it, then: ollama pull " + TIERS[tier]
        )
    # Match on the bare tag too, so "gpt-oss:20b" finds "gpt-oss:20b" however
    # Ollama chose to report it (some versions append/omit ":latest").
    bare = {m.split(":")[0]: m for m in available}
    for candidate in FALLBACKS[tier]:
        if candidate in available:
            return candidate
        name = candidate.split(":")[0]
        if candidate.endswith(":latest") and name in bare:
            return bare[name]
    raise LocalLLMError(
        f"no model available for tier {tier!r}. Pull the preferred one with: "
        f"ollama pull {TIERS[tier]}"
    )


def ask(
    prompt: str,
    system: str = "",
    tier: str = "default",
    model: str | None = None,
    think: bool = False,
    json_mode: bool = False,
    timeout: float | None = None,
) -> str:
    """Run one bounded text task locally and return the reply text.

    Args:
        prompt: the task and its content.
        system: optional system prompt to steer behavior.
        tier: which model class to use - see TIERS. Ignored if `model` is set.
        model: explicit Ollama tag, overriding `tier`.
        think: let a reasoning model emit its <think> block. Off by default;
            these tasks don't need it and it wastes time and tokens.
        json_mode: ask Ollama to constrain output to valid JSON.
        timeout: seconds to wait. Defaults to longer for models that load
            partly from disk, so a cold start isn't mistaken for a failure.

    Raises:
        LocalLLMError: Ollama unreachable, no suitable model, or empty reply.
    """
    tag = model or pick_model(tier)
    if timeout is None:
        timeout = LARGE_MODEL_TIMEOUT if tag.startswith(LARGE_MODEL_PREFIXES) else REQUEST_TIMEOUT

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {"model": tag, "messages": messages, "stream": False}
    if not think:
        # gpt-oss can't switch reasoning off: it takes an effort level, and
        # silently ignores think=False (measured: it reasoned MORE with it set).
        # "low" is the floor. qwen3 and friends honor a plain boolean.
        payload["think"] = "low" if tag.startswith("gpt-oss") else False
    if json_mode:
        payload["format"] = "json"

    try:
        resp = httpx.post(CHAT_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise LocalLLMError(
            f"could not reach Ollama at {OLLAMA_HOST} - is it running?"
        ) from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()[:300]
        # Older Ollama builds reject the "think" parameter outright; retry once
        # without it rather than making every caller handle the version skew.
        if not think and "think" in detail.lower():
            payload.pop("think", None)
            resp = httpx.post(CHAT_URL, json=payload, timeout=timeout)
            resp.raise_for_status()
        else:
            raise LocalLLMError(f"Ollama returned {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise LocalLLMError(f"Ollama request failed: {exc}") from exc

    content = (resp.json().get("message") or {}).get("content", "")
    content = _THINK_RE.sub("", content).strip()
    if not content:
        raise LocalLLMError(f"model {tag} returned an empty reply")
    return content


def ask_json(
    prompt: str,
    system: str = "",
    tier: str = "default",
    model: str | None = None,
    timeout: float | None = None,
) -> Any:
    """Same as ask() but parses the reply as JSON.

    Local models sometimes fence their JSON despite format=json, so strip a
    ```json fence before parsing rather than failing the whole call.
    """
    raw = ask(
        prompt, system=system, tier=tier, model=model, json_mode=True, timeout=timeout
    )
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LocalLLMError(
            f"model reply was not valid JSON ({exc}); got: {text[:300]}"
        ) from exc


def condense(text: str, instruction: str, max_chars: int = 60_000, tier: str = "default") -> str:
    """Shrink long tool output locally before a Claude agent reads it.

    The token-saving workhorse: pass a wall of queue/history/health output and
    get back only what the instruction asked for. Input is truncated at
    max_chars so a runaway log can't blow the model's context window.

    Never use this on output a decision hinges on being complete and exact -
    the point is a readable digest, and a local model will drop detail.

    Defaults to the "default" tier on purpose. The "fast" tier was tested on a
    small download queue and reproducibly reported a finished, seeding torrent
    as stalled - a digest that confidently mislabels state is worse than
    reading the raw output.
    """
    clipped = text[:max_chars]
    truncated = len(text) > max_chars
    system = (
        "You condense machine output for an engineer. Report only what is "
        "present in the input. Never invent entries, counts, names, or "
        "statuses. If the input does not answer the instruction, say so "
        "plainly. Be terse - no preamble, no closing summary."
    )
    note = "\n\n[input truncated]" if truncated else ""
    return ask(f"{instruction}\n\n---\n{clipped}{note}", system=system, tier=tier)


def ollama_running() -> bool:
    """True if Ollama answers on localhost. Cheap pre-flight for callers."""
    try:
        httpx.get(TAGS_URL, timeout=5.0).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def ensure_model(tag: str, pull: bool = False, timeout: float = 3600.0) -> bool:
    """Check a model is pulled, optionally pulling it.

    Pulling is opt-in because it can mean a multi-gigabyte download - callers
    that run unattended should leave pull=False and report the gap instead.
    """
    if tag in installed_models():
        return True
    if not pull:
        return False
    try:
        subprocess.run(["ollama", "pull", tag], check=True, timeout=timeout)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise LocalLLMError(f"ollama pull {tag} failed: {exc}") from exc
    return tag in installed_models()


if __name__ == "__main__":
    import sys

    if not ollama_running():
        print(f"Ollama not reachable at {OLLAMA_HOST}", file=sys.stderr)
        raise SystemExit(1)
    have = installed_models()
    print(f"Ollama up at {OLLAMA_HOST} with {len(have)} model(s):")
    for tag in sorted(have):
        print(f"  {tag}")
    print("\nTier resolution:")
    for tier in TIERS:
        try:
            print(f"  {tier:<8} -> {pick_model(tier, available=have)}")
        except LocalLLMError as exc:
            print(f"  {tier:<8} -> unavailable ({exc})")
