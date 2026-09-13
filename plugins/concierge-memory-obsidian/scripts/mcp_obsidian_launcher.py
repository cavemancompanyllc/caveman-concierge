"""Loads .env from the instance root, then runs the mcp-obsidian server
in-process (not via `uvx`) so we can patch two known upstream gaps before
it starts.

This script ships inside the `concierge-memory-obsidian` plugin, so it does
NOT live in the instance's own repo — .env lives there, not next to this
file. The instance root resolves via the `CONCIERGE_HOME` env var (the
plugin's own `.mcp.json` sets this to `${CLAUDE_PROJECT_DIR}` before
launching this process), falling back to cwd for manual/local-dev runs
where that isn't set.

Most config (vault path, API key, host/port) actually arrives as real
process env vars already, via the plugin's `userConfig` -> `.mcp.json` env
mapping — this .env load is a convenience fallback for values set that way
instead, and `setdefault` means it never overrides what's already set.

1. mcp-obsidian 0.2.2 has no upper bound on its `mcp` dependency, so a bare
   `uvx mcp-obsidian` used to pull whatever the latest mcp SDK is. mcp 2.0.0
   removed/renamed the Server.list_tools decorator that mcp-obsidian 0.2.2
   was built against, breaking the server at import time (AttributeError:
   'Server' object has no attribute 'list_tools'). Fixed by installing
   mcp-obsidian into this same environment (pip, see requirements.txt)
   instead of letting uvx resolve a fresh, unpinned one.

2. The Obsidian Local REST API plugin v5.0.3 ("Local REST API with MCP")
   deprecated the old header-based PATCH format and now requires an
   explicit `Markdown-Patch-Version: 1` request header to use it —
   without it, `patch_content` fails with `Error 40084: Header-based PATCH
   targeting is ambiguous...`. mcp-obsidian 0.2.2 (the latest PyPI release
   — checked, nothing newer exists) never sends that header. Monkeypatched
   in below until mcp-obsidian ships a fix upstream.
"""
import asyncio
import os

ROOT = os.environ.get("CONCIERGE_HOME") or os.getcwd()
ENV_PATH = os.path.join(ROOT, ".env")

if os.path.exists(ENV_PATH):
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            os.environ.setdefault(key, value)

from mcp_obsidian import obsidian, server  # noqa: E402 (needs env vars loaded first)

_orig_patch_content = obsidian.Obsidian.patch_content


def _patch_content_with_version_header(self, filepath, operation, target_type, target, content):
    headers = self._get_headers() | {
        "Content-Type": "text/markdown",
        "Operation": operation,
        "Target-Type": target_type,
        "Target": __import__("urllib.parse", fromlist=["quote"]).quote(target),
        "Markdown-Patch-Version": "1",
    }

    def call_fn():
        import requests
        url = f"{self.get_base_url()}/vault/{filepath}"
        response = requests.patch(url, headers=headers, data=content, verify=self.verify_ssl, timeout=self.timeout)
        response.raise_for_status()
        return None

    return self._safe_call(call_fn)


obsidian.Obsidian.patch_content = _patch_content_with_version_header

asyncio.run(server.main())
