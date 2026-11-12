---
name: guidebook
description: Keep the Caveman Guidebook (docs/guidebook/guidebook.html) in step with the plugins. Review mode updates the guidebook text for what the unpushed commits change and commits it; publish mode rebuilds the page from main and republishes its claude.ai link. Use for "update the guidebook", "review the guidebook", "publish the guidebook", or when a hook holds a git push or says the published guidebook is behind main.
argument-hint: "[publish]"
---

# guidebook

The Caveman Guidebook is the user's guide to Caveman Core and the public concierge plugins:
setup, workspaces, a working session, one card per plugin, rules, troubleshooting, the CLI,
a glossary, release notes and a full reference. Its text is `docs/guidebook/guidebook.html`;
`docs/guidebook/guidebook.json` holds the published page's `url`.

This repo's hooks (`.claude/settings.json`) keep it current:

| When | Hook | What to run |
|---|---|---|
| A `git push` from Claude Code that changes files the guidebook is written from | `guidebook.py pre-push` holds the push | **Review mode**, then the same push again |
| After a push, when the published page is behind main | `guidebook.py post-push` says so | **Publish mode** |

**Never written by hand:** the commit and date stamps, plugin versions and item counts, the
plugin count, the core version in the install command, the release notes and the reference.
`guidebook.py build` fills every element with a `data-gb="..."` attribute at publish time. In
the committed page they read `set at publish`, `v?` or `?`; leave them that way.

With no argument, run review mode. With `publish`, run publish mode.

---

# Review mode

## R1. What changed

```bash
python tools/guidebook/guidebook.py status --json
```

- `files` empty, or `checked: true`: nothing to review. Say so and stop (if a push was held,
  run it again).
- Otherwise read `commits` and `files`, then the diffs: `git diff <merge_base>..HEAD -- <file>`.
- `fetched: false`: the fetch failed, so the base is as of the last fetch. Say so.

## R2. Which sections it touches

| Source | Guidebook section (`id`) |
|---|---|
| `README.md`, `docs/getting-started.md` | `setup`, `workspace` |
| `.claude-plugin/marketplace.json`, a plugin's `plugin.json` description, its `skills/`, `agents/`, `commands/`, `requires.json` | that plugin's card in `plugins`: description, need chips, setup line, example requests |
| A plugin's settings (`workspace.yaml` keys it reads, env vars, `config/*.example.json`) | that card's setup line; `workspace` if the pattern is new |
| `plugins/*/rules/session.md` | `rules` |
| Scheduled-job scripts, `.allowedtools` conventions | `unattended` |
| A trap found in practice | `trouble` |
| `docs/writing-a-plugin.md` | `maintain` |
| A new plugin | a new `<article class="card">` in `plugins` with `data-gb="version-<name>"` and `data-gb="items-<name>"` spans like the others |
| A removed plugin | remove its card |

Caveman Core's own changes (its repo is separate) belong in `start`, `pieces`, `workspace`,
`session` and `commands`; update those when core's behaviour or CLI changes.

A change no reader would notice (refactor, tests, a version bump, a fix that keeps behaviour)
needs no edit. Say so, skip R3, and run R4.

## R3. Edit and commit

Edit `docs/guidebook/guidebook.html` with the Edit tool, section by section. Keep the structure,
the CSS tokens and the `data-gb` placeholders; change content.

- **Write for a newcomer.** Plain words, short sentences, what a thing is for before how it
  works. Define new terms in the glossary.
- **US English, no em dashes** in the copy.
- **Only what is in this repo's commits** (and released core). Never describe unreleased work.
- **Public plugins only.** Never mention private plugins or marketplaces.
- **Nothing personal or client-related.** No real names, family details, league names, account
  ids, home paths, employers or clients. Examples use generic people and places
  (`~/Vaults/home`, "the plumber", "the garden project").
- **Never write release notes, versions or counts by hand.**
- Every color comes from the `:root` tokens; no transparent backgrounds.

Commit the page alone, whatever else is in the working tree:

```bash
git add docs/guidebook/guidebook.html
git commit -m "docs: guidebook for <what changed>"
```

## R4. Record the review

```bash
python tools/guidebook/guidebook.py reviewed
```

This writes a fingerprint of the source files to `.git/` only. The held push now goes through;
a later commit that changes a source file makes the next push review again.

## R5. Report and push

One line per section changed, or "no guidebook change needed" and why. Then run the push the
hook held, exactly as it was. The user reads the guidebook diff before it is pushed: the text is
AI-drafted.

---

# Publish mode

## P1. Build from main

```bash
python tools/guidebook/guidebook.py build
```

Writes `docs/guidebook/_site/guidebook.html` (gitignored) from the upstream branch
(`origin/main`), with every stamp filled. Caveman Core's card and reference come from a sibling
clone at `../caveman-core` when it exists.

- `changed: false`: this commit was already published. Say so and stop.
- `no_card` not empty: a plugin has no card. Publish anyway, tell the user, and add the card in
  review mode next time.
- `unfilled` not empty: a placeholder had no value. Report it.
- `core_included: false`: no `../caveman-core` clone, so core's card shows `v?`. Say so.

## P2. Publish

Publish `docs/guidebook/_site/guidebook.html` with the Artifact tool:

- `url` from the build output when it is set. Follow the Artifact tool's rules for an artifact
  this session has not read. If the publish is refused because the user can't edit it, say so
  in one line and stop.
- `url` empty: first publish, with `icon: "book"`. Then record the returned link in
  `docs/guidebook/guidebook.json` and commit it (`docs: guidebook link`).

Sharing is the owner's choice, made from the page's Share menu.

## P3. Record

```bash
python tools/guidebook/guidebook.py published <commit from P1>
```

One line to the user: published main `<commit>` to the link.
