"""Tests for guidebook.py: run with `python -m pytest tools/guidebook`."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import guidebook as gb  # noqa: E402

PAGE = """<title>T</title>
<span data-gb="commit">set at publish</span> <b data-gb="date">set at publish</b>
<span data-gb="version-caveman-core">v?</span> <span data-gb="core-tag">0.0.0</span>
<p><span data-gb="plugin-count">set at publish</span> plugins</p>
<article><span data-gb="version-alpha">v?</span><span data-gb="items-alpha">?</span></article>
<div data-gb="release-notes"><p>set at publish</p></div>
<div data-gb="reference"><p>set at publish</p></div>
"""


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo, rel, text):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def commit_all(repo, msg):
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", msg)


def plugin(repo, name, version="1.0.0"):
    root = f"plugins/{name}"
    write(repo, f"{root}/.claude-plugin/plugin.json", json.dumps({"name": name, "version": version,
                                                                   "description": f"{name} does things"}))
    write(repo, f"{root}/skills/thing/SKILL.md", "---\nname: thing\ndescription: >\n  Does the\n  thing.\n---\nbody\n")
    write(repo, f"{root}/agents/helper.md", "---\nname: helper\ndescription: \"Helps.\"\n---\n")
    write(repo, f"{root}/commands/go.md", "---\ndescription: Go now.\nargument-hint: \"[fast]\"\n---\n")
    write(repo, f"{root}/.mcp.json", json.dumps({"mcpServers": {"srv": {"command": "python"}}}))


@pytest.fixture
def repos(tmp_path):
    """A bare origin, a clone with one plugin and the guidebook, and a core repo."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "concierge"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for r in (repo,):
        sh(r, "config", "user.email", "t@example.com")
        sh(r, "config", "user.name", "T")
        sh(r, "checkout", "-q", "-b", "main")
    write(repo, ".claude-plugin/marketplace.json", json.dumps(
        {"name": "mkt", "plugins": [{"name": "alpha", "source": "./plugins/alpha"}]}))
    plugin(repo, "alpha")
    write(repo, "docs/guidebook/guidebook.html", PAGE)
    write(repo, "docs/guidebook/guidebook.json", json.dumps({"url": "https://example.com/a"}))
    commit_all(repo, "feat: alpha plugin")
    sh(repo, "push", "-q", "-u", "origin", "main")

    core = tmp_path / "caveman-core"
    core.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(core)], check=True)
    sh(core, "config", "user.email", "t@example.com")
    sh(core, "config", "user.name", "T")
    write(core, "plugins/caveman-core/.claude-plugin/plugin.json", json.dumps({"name": "caveman-core", "version": "9.9.9"}))
    write(core, "plugins/caveman-core/hooks/hooks.json", json.dumps({"hooks": {"Stop": [], "SessionStart": []}}))
    commit_all(core, "core")
    return repo, core


def push_payload(cmd):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})


@pytest.mark.parametrize("cmd,hit", [
    ("git push", True), ("git push origin main --tags", True), ("git -C /x push", True),
    ("cd repo && git push -q origin main", True), ("git -c a.b=c push", True),
    ("git status", False), ("git pushx", False), ("echo pushing", False), ("git log --format=push", False),
])
def test_push_regex(cmd, hit):
    assert bool(gb.PUSH_RE.search(cmd)) is hit


def test_status_review_and_pre_push(repos):
    repo, _ = repos
    assert gb.status(repo, fetch=False)["files"] == []
    assert gb.hook_pre_push(push_payload("git push"), repo) == ""

    # a source change, unpushed: push is held
    plugin(repo, "alpha", "1.1.0")
    commit_all(repo, "feat(alpha): faster")
    st = gb.status(repo, fetch=False)
    assert st["files"] == ["plugins/alpha/.claude-plugin/plugin.json"] and not st["checked"]
    out = json.loads(gb.hook_pre_push(push_payload("git push origin main"), repo))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "guidebook" in out["hookSpecificOutput"]["permissionDecisionReason"]

    # reviewed, plus a guidebook-only commit afterwards: push goes through
    gb.record_reviewed(repo)
    write(repo, "docs/guidebook/guidebook.html", PAGE + "<p>more</p>")
    commit_all(repo, "docs: guidebook for alpha")
    assert gb.hook_pre_push(push_payload("git push"), repo) == ""

    # another source change after the review: held again
    write(repo, "README.md", "changed")
    commit_all(repo, "docs: readme")
    assert gb.hook_pre_push(push_payload("git push"), repo)

    # a push aimed at another repo is none of our business
    assert gb.hook_pre_push(push_payload("git -C /somewhere/else push"), repo) == ""
    # broken payload never raises
    assert gb.hook_pre_push("not json", repo) == ""


def test_post_push_reminds_until_published(repos):
    repo, _ = repos
    msg = json.loads(gb.hook_post_push(push_payload("git push"), repo))
    assert "publish mode" in msg["hookSpecificOutput"]["additionalContext"]
    head = gb.git("rev-parse", "origin/main", repo=repo).strip()
    gb.marker("published", repo).write_text(head, encoding="utf-8")
    assert gb.hook_post_push(push_payload("git push"), repo) == ""
    assert gb.hook_post_push(push_payload("git status"), repo) == ""


def test_inventory_reads_frontmatter(repos):
    repo, core = repos
    inv = gb.inventory("origin/main", repo, core)
    alpha = inv["plugins"][0]
    assert alpha["version"] == "1.0.0"
    assert alpha["items"]["skills"][0] == {"name": "thing", "call": "alpha:thing", "description": "Does the thing."}
    assert alpha["items"]["commands"][0]["call"] == "/alpha:go" and alpha["items"]["commands"][0]["args"] == "[fast]"
    assert alpha["items"]["agents"][0]["description"] == "Helps."
    assert alpha["items"]["mcp"] == [{"name": "srv"}]
    assert inv["core"]["version"] == "9.9.9" and [h["name"] for h in inv["core"]["items"]["hooks"]] == ["Stop", "SessionStart"]


def test_build_fills_every_placeholder(repos):
    repo, core = repos
    write(repo, "docs/guidebook/guidebook.html", PAGE + "<!-- edited -->")
    commit_all(repo, "docs: guidebook for nothing")    # excluded from release notes
    plugin(repo, "alpha", "1.2.0")
    commit_all(repo, "feat(alpha): <new> & better")
    sh(repo, "push", "-q")
    res = gb.build("origin/main", repo, core)
    page = Path(res["out"]).read_text(encoding="utf-8")
    assert res["unfilled"] == [] and res["no_card"] == [] and res["changed"] and res["core_included"]
    assert res["url"] == "https://example.com/a"
    assert "set at publish" not in page and "v?" not in page
    assert '<span data-gb="version-alpha">v1.2.0</span>' in page
    assert "1 skill · 1 agent · 1 command · 1 MCP server" in page
    assert '<span data-gb="core-tag">9.9.9</span>' in page
    assert "feat(alpha): &lt;new&gt; &amp; better" in page and "docs: guidebook for nothing" not in page
    assert '<span class="chip">alpha</span>' in page
    assert "<code>/alpha:go [fast]</code>" in page and 'id="ref-caveman-core"' in page


def test_build_reports_missing_card(repos):
    repo, core = repos
    mkt = {"name": "mkt", "plugins": [{"name": "alpha", "source": "./plugins/alpha"},
                                      {"name": "beta", "source": "./plugins/beta"}]}
    write(repo, ".claude-plugin/marketplace.json", json.dumps(mkt))
    plugin(repo, "beta")
    commit_all(repo, "feat: beta")
    sh(repo, "push", "-q")
    assert gb.build("origin/main", repo, core)["no_card"] == ["beta"]
