"""Keep the Caveman Guidebook in step with the plugins.

The guidebook's text is ``docs/guidebook/guidebook.html``, written by hand (with
Claude) and reviewed like code. Everything that would go stale or conflict is
generated at publish time from the repos instead: plugin versions and item
counts, the release notes and the full reference of every skill, agent,
command, MCP server and hook.

Commands (``python tools/guidebook/guidebook.py <cmd>``):

    status [--json]     what this branch changes that the guidebook is written from
    reviewed            record that the guidebook was reviewed for the current files
    pre-push            PreToolUse hook: hold a `git push` until the review is recorded
    post-push           PostToolUse hook: after a push, say when the published page is behind
    inventory [--ref R] JSON of every plugin and its items (what the build reads)
    build [--ref R]     write docs/guidebook/_site/guidebook.html with every stamp filled
    published <commit>  record that the page was published from <commit>

Markers live in ``.git/`` (never committed): ``guidebook-reviewed`` holds the
fingerprint of the source files at the last review, ``guidebook-published`` the
commit last published. Standard library plus PyYAML (a caveman-core dependency).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = Path("docs/guidebook/guidebook.html")
META = Path("docs/guidebook/guidebook.json")
SITE = Path("docs/guidebook/_site")
SOURCES = ("README.md", ".claude-plugin", "plugins", "docs")
NOT_SOURCES = ("docs/guidebook/",)
CORE_REPO_DEFAULT = REPO.parent / "caveman-core"
PENDING = "set at publish"
NOTES_COMMITS = 25


# ------------------------------------------------------------------ git

def git(*args: str, repo: Path = REPO, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def git_dir(repo: Path = REPO) -> Path:
    return Path(git("rev-parse", "--absolute-git-dir", repo=repo).strip())


def is_source(path: str) -> bool:
    return (path == "README.md" or path.startswith((".claude-plugin/", "plugins/", "docs/"))) \
        and not path.startswith(NOT_SOURCES)


def fingerprint(ref: str = "HEAD", repo: Path = REPO) -> str:
    """Hash of every source file's blob id at ``ref``: changes only when a source changes."""
    lines = [ln for ln in git("ls-tree", "-r", ref, "--", *SOURCES, repo=repo).splitlines()
             if is_source(ln.split("\t", 1)[1])]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def upstream(repo: Path = REPO) -> str:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "@{upstream}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "origin/main"


def marker(name: str, repo: Path = REPO) -> Path:
    return git_dir(repo) / f"guidebook-{name}"


def read_marker(name: str, repo: Path = REPO) -> str:
    f = marker(name, repo)
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


# ------------------------------------------------------------------ status / review

def status(repo: Path = REPO, fetch: bool = True) -> dict:
    fetched = True
    if fetch:
        fetched = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], capture_output=True).returncode == 0
    base_ref = upstream(repo)
    try:
        base = git("merge-base", base_ref, "HEAD", repo=repo).strip()
    except RuntimeError:
        base = ""
    rng = f"{base}..HEAD" if base else "HEAD"
    files = sorted({f for f in git("diff", "--name-only", rng, repo=repo).splitlines() if is_source(f)}) if base else []
    commits = git("log", "--format=%h %s", rng, repo=repo).splitlines() if base else []
    fp = fingerprint("HEAD", repo)
    return {"base": base_ref, "merge_base": base[:12], "fetched": fetched, "commits": commits, "files": files,
            "fingerprint": fp, "checked": not files or read_marker("reviewed", repo) == fp}


def record_reviewed(repo: Path = REPO) -> str:
    fp = fingerprint("HEAD", repo)
    marker("reviewed", repo).write_text(fp + "\n", encoding="utf-8")
    return fp


# ------------------------------------------------------------------ hooks

PUSH_RE = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+push\b")


def _hook_command(stdin_text: str) -> str:
    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        return ""
    return str((payload.get("tool_input") or {}).get("command") or "")


def _targets_this_repo(command: str, repo: Path = REPO) -> bool:
    m = re.search(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
    if not m:
        return True
    try:
        return Path(m.group(1).strip("\"'")).resolve() == repo.resolve()
    except OSError:
        return False


def hook_pre_push(stdin_text: str, repo: Path = REPO) -> str:
    command = _hook_command(stdin_text)
    if not PUSH_RE.search(command) or not _targets_this_repo(command, repo):
        return ""
    st = status(repo)
    if st["checked"]:
        return ""
    reason = ("Push held: this push changes files the Caveman Guidebook is written from "
              f"({', '.join(st['files'][:8])}{' ...' if len(st['files']) > 8 else ''}). "
              "Run the `guidebook` skill in review mode first (update docs/guidebook/guidebook.html for "
              "what changed and commit it, or confirm no change is needed), then run the same push again.")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def hook_post_push(stdin_text: str, repo: Path = REPO) -> str:
    command = _hook_command(stdin_text)
    if not PUSH_RE.search(command) or not _targets_this_repo(command, repo):
        return ""
    try:
        head = git("rev-parse", upstream(repo), repo=repo).strip()
    except RuntimeError:
        return ""
    if read_marker("published", repo) == head:
        return ""
    msg = ("The Caveman Guidebook's published page is behind main. Run the `guidebook` skill in publish mode "
           "to rebuild it and republish the same link (needs edit access to the page).")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}})


# ------------------------------------------------------------------ inventory

class Tree:
    """Read files from a git ref (or the working tree when ref is None)."""

    def __init__(self, repo: Path, ref: str | None):
        self.repo, self.ref = repo, ref
        self.files = (git("ls-tree", "-r", "--name-only", ref, repo=repo).splitlines() if ref
                      else [p.relative_to(repo).as_posix() for p in repo.rglob("*")
                            if p.is_file() and ".git" not in p.parts])

    def read(self, path: str) -> str | None:
        if self.ref:
            r = subprocess.run(["git", "-C", str(self.repo), "show", f"{self.ref}:{path}"],
                               capture_output=True, text=True, encoding="utf-8")
            return r.stdout if r.returncode == 0 else None
        f = self.repo / path
        return f.read_text(encoding="utf-8") if f.is_file() else None

    def glob(self, prefix: str, pattern: str) -> list[str]:
        rx = re.compile("^" + re.escape(prefix) + pattern + "$")
        return sorted(f for f in self.files if rx.match(f))


def frontmatter(text: str | None) -> dict:
    if not text or not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    import yaml  # imported here: the hooks run on every shell call and never need it
    try:
        data = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _one_line(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def plugin_items(tree: Tree, root: str, name: str) -> dict:
    manifest = json.loads(tree.read(f"{root}/.claude-plugin/plugin.json") or "{}")
    items: dict[str, list] = {"skills": [], "agents": [], "commands": [], "mcp": [], "hooks": []}
    for f in tree.glob(f"{root}/skills/", r"[^/]+/SKILL\.md"):
        fm = frontmatter(tree.read(f))
        sk = fm.get("name") or f.split("/")[-2]
        items["skills"].append({"name": sk, "call": f"{name}:{sk}", "description": _one_line(fm.get("description"))})
    for f in tree.glob(f"{root}/agents/", r"[^/]+\.md"):
        fm = frontmatter(tree.read(f))
        ag = fm.get("name") or Path(f).stem
        items["agents"].append({"name": ag, "call": f"{name}:{ag}", "description": _one_line(fm.get("description"))})
    for f in tree.glob(f"{root}/commands/", r"[^/]+\.md"):
        fm = frontmatter(tree.read(f))
        cmd = Path(f).stem
        items["commands"].append({"name": cmd, "call": f"/{name}:{cmd}", "description": _one_line(fm.get("description")),
                                  "args": _one_line(fm.get("argument-hint"))})
    mcp = json.loads(tree.read(f"{root}/.mcp.json") or "{}").get("mcpServers") or manifest.get("mcpServers") or {}
    if isinstance(mcp, dict):
        items["mcp"] = [{"name": k} for k in sorted(mcp)]
    hooks = json.loads(tree.read(f"{root}/hooks/hooks.json") or "{}").get("hooks") or {}
    items["hooks"] = [{"name": k} for k in hooks]
    return {"name": name, "version": manifest.get("version", "?"), "description": _one_line(manifest.get("description")),
            "items": items}


def inventory(ref: str | None = None, repo: Path = REPO, core_repo: Path | None = CORE_REPO_DEFAULT) -> dict:
    tree = Tree(repo, ref)
    mkt = json.loads(tree.read(".claude-plugin/marketplace.json") or "{}")
    plugins = []
    for p in mkt.get("plugins", []):
        src = p.get("source")
        if isinstance(src, str) and src.startswith("./"):
            plugins.append(plugin_items(tree, src[2:].rstrip("/"), p["name"]))
    core = None
    if core_repo and (core_repo / ".git").exists():
        core_ref = None
        if ref:
            core_ref = upstream(core_repo) if ref != "HEAD" else "HEAD"
            if subprocess.run(["git", "-C", str(core_repo), "rev-parse", "--verify", "-q", core_ref],
                              capture_output=True).returncode != 0:
                core_ref = "HEAD"     # a clone with no upstream: use what is checked out
        core = plugin_items(Tree(core_repo, core_ref), "plugins/caveman-core", "caveman-core")
    return {"marketplace": mkt.get("name"), "core": core, "plugins": plugins}


# ------------------------------------------------------------------ build

def _counts(items: dict) -> str:
    parts = []
    for key, one in (("skills", "skill"), ("agents", "agent"), ("commands", "command"), ("mcp", "MCP server")):
        n = len(items[key])
        if n:
            parts.append(f"{n} {one}{'s' if n != 1 else ''}")
    return " · ".join(parts) or "hooks only"


def fill(page: str, key: str, value: str) -> str:
    """Replace the text inside every element carrying data-gb="key"."""
    rx = re.compile(r'(<(\w+)\b[^>]*\bdata-gb="' + re.escape(key) + r'"[^>]*>)(.*?)(</\2>)', re.S)
    return rx.sub(lambda m: m.group(1) + value + m.group(4), page)


def release_notes(repo: Path, ref: str, n: int = NOTES_COMMITS) -> str:
    out = git("log", ref, f"-n{n * 3}", "--date=short", "--format=@@%h\t%ad\t%s", "--name-only", repo=repo)
    entries = []
    for block in out.split("@@")[1:]:
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        sha, date, subject = lines[0].split("\t", 2)
        touched = sorted({f.split("/")[1] for f in lines[1:] if f.startswith("plugins/") and f.count("/") >= 2})
        if not any(is_source(f) for f in lines[1:]) or re.match(r"docs: guidebook\b", subject):
            continue
        entries.append((date, sha, subject, touched))
        if len(entries) >= n:
            break
    if not entries:
        return "<p>No changes yet.</p>"
    rows = []
    for date, sha, subject, touched in entries:
        chips = "".join(f'<span class="chip">{html.escape(t)}</span>' for t in touched)
        rows.append(f'<li><time>{date}</time><span class="note-text">{html.escape(subject)}</span>{chips}'
                    f'<code class="sha">{sha}</code></li>')
    return '<ol class="notes">' + "".join(rows) + "</ol>"


def reference_html(inv: dict) -> str:
    blocks = []
    for p in ([inv["core"]] if inv.get("core") else []) + inv["plugins"]:
        rows = []
        for kind, label in (("commands", "Command"), ("skills", "Skill"), ("agents", "Agent")):
            for it in p["items"][kind]:
                call = it["call"] + (f" {it['args']}" if it.get("args") else "")
                rows.append(f"<tr><td><span class=\"kind kind-{kind}\">{label}</span></td>"
                            f"<td><code>{html.escape(call)}</code></td><td>{html.escape(it['description'])}</td></tr>")
        for it in p["items"]["mcp"]:
            rows.append(f"<tr><td><span class=\"kind kind-mcp\">MCP</span></td><td><code>{html.escape(it['name'])}</code>"
                        f"</td><td>Tools from this server appear as <code>mcp__plugin_{html.escape(p['name'])}_"
                        f"{html.escape(it['name'])}__*</code>.</td></tr>")
        if p["items"]["hooks"]:
            ev = ", ".join(h["name"] for h in p["items"]["hooks"])
            rows.append(f"<tr><td><span class=\"kind kind-hooks\">Hooks</span></td><td><code>{html.escape(ev)}</code>"
                        "</td><td>Run automatically on these events.</td></tr>")
        body = "".join(rows) or '<tr><td colspan="3">Nothing to call directly.</td></tr>'
        blocks.append(f'<details class="ref" id="ref-{p["name"]}"><summary><span class="ref-name">{p["name"]}</span>'
                      f'<span class="ref-ver">v{html.escape(p["version"])}</span>'
                      f'<span class="ref-count">{_counts(p["items"])}</span></summary>'
                      f'<div class="table-wrap"><table><thead><tr><th>Kind</th><th>Call it</th><th>What it does</th>'
                      f'</tr></thead><tbody>{body}</tbody></table></div></details>')
    return "".join(blocks)


def build(ref: str | None = None, repo: Path = REPO, core_repo: Path | None = CORE_REPO_DEFAULT,
          page_text: str | None = None) -> dict:
    ref = ref or upstream(repo)
    page = page_text if page_text is not None else Tree(repo, ref).read(PAGE.as_posix())
    if page is None:
        raise RuntimeError(f"{PAGE} does not exist at {ref}")
    inv = inventory(ref, repo, core_repo)
    commit = git("rev-parse", ref, repo=repo).strip()
    date = git("log", "-1", "--format=%cs", ref, repo=repo).strip()
    page = fill(page, "commit", commit[:7])
    d = datetime.strptime(date, "%Y-%m-%d")
    page = fill(page, "date", f"{d:%B} {d.day}, {d.year}")
    page = fill(page, "plugin-count", str(len(inv["plugins"])))
    if inv["core"]:
        page = fill(page, "core-tag", html.escape(inv["core"]["version"]))
    all_plugins = ([inv["core"]] if inv["core"] else []) + inv["plugins"]
    no_card = []
    for p in all_plugins:
        if f'data-gb="version-{p["name"]}"' not in page:
            no_card.append(p["name"])
        page = fill(page, f"version-{p['name']}", f"v{html.escape(p['version'])}")
        page = fill(page, f"items-{p['name']}", _counts(p["items"]))
    page = fill(page, "release-notes", release_notes(repo, ref))
    page = fill(page, "reference", reference_html(inv))
    leftover = sorted(set(re.findall(r'data-gb="([^"]+)"[^>]*>' + re.escape(PENDING), page)))
    out = repo / SITE / "guidebook.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    meta = json.loads((repo / META).read_text(encoding="utf-8")) if (repo / META).is_file() else {}
    return {"out": str(out), "url": meta.get("url", ""), "commit": commit, "date": date,
            "changed": read_marker("published", repo) != commit, "no_card": no_card, "unfilled": leftover,
            "core_included": bool(inv["core"])}


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="guidebook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("status"); x.add_argument("--json", action="store_true"); x.add_argument("--no-fetch", action="store_true")
    sub.add_parser("reviewed")
    sub.add_parser("pre-push")
    sub.add_parser("post-push")
    x = sub.add_parser("inventory"); x.add_argument("--ref")
    x = sub.add_parser("build"); x.add_argument("--ref"); x.add_argument("--core", default=str(CORE_REPO_DEFAULT))
    x = sub.add_parser("published"); x.add_argument("commit")
    a = ap.parse_args(argv)

    if a.cmd in ("pre-push", "post-push"):
        try:
            data = sys.stdin.buffer.read().decode("utf-8", "replace")
            out = hook_pre_push(data) if a.cmd == "pre-push" else hook_post_push(data)
        except Exception:  # noqa: BLE001 - a hook must never break the session
            out = ""
        if out:
            print(out)
        return 0
    if a.cmd == "status":
        st = status(fetch=not a.no_fetch)
        if a.json:
            print(json.dumps(st, indent=2))
        else:
            print(f"base {st['base']} (merge base {st['merge_base']}){'' if st['fetched'] else ' - fetch failed'}")
            print(f"{len(st['commits'])} commit(s), {len(st['files'])} source file(s) changed; "
                  f"{'reviewed' if st['checked'] else 'NOT reviewed'}")
            for f in st["files"]:
                print(f"  {f}")
        return 0
    if a.cmd == "reviewed":
        print(f"reviewed {record_reviewed()}")
        return 0
    if a.cmd == "inventory":
        print(json.dumps(inventory(a.ref), indent=2))
        return 0
    if a.cmd == "build":
        print(json.dumps(build(a.ref, core_repo=Path(a.core)), indent=2))
        return 0
    if a.cmd == "published":
        marker("published").write_text(git("rev-parse", a.commit).strip() + "\n", encoding="utf-8")
        print("recorded")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
