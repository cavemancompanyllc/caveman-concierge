# Bootstrapping a new instance

An instance is a thin directory: your persona, your facts, your secrets,
your data. All capability comes from plugins.

1. Copy this whole `instance-template/` directory to wherever you want
   your instance to live (its own git repo, ideally — see the main
   [README](../README.md) for why one product repo + many thin instance
   repos is the recommended shape).
2. `cd` into it and run:
   ```bash
   claude plugin marketplace add cavemancompanyllc/caveman-concierge
   claude plugin install concierge-core@caveman-concierge --scope project
   claude plugin install concierge-git@caveman-concierge --scope project
   ```
3. Edit `CLAUDE.md` — fill in the placeholders (who you are, what this
   instance is for, an orchestrator name if you want one).
4. Copy `.env.example` to `.env` and fill in only what the plugins you
   plan to install actually need.
5. Install whichever other plugins you want:
   ```bash
   claude plugin install concierge-memory-obsidian@caveman-concierge --scope project \
     --config vault_path="/path/to/your/vault" \
     --config api_key="..."
   ```
   See each plugin's own README/description
   (`claude plugin details <name>@caveman-concierge`) for its exact
   `userConfig` options.
6. Start a session in the instance directory. Ask it what it can do —
   the installed agents introduce themselves via `/context` or by name.

See [docs/getting-started.md](../docs/getting-started.md) in the main repo
for a fuller walkthrough, and [docs/agent-catalog.md](../docs/agent-catalog.md)
for what every available plugin actually does before you decide what to
install.
