#!/usr/bin/env bash
# Scans staged git changes for likely secrets/credentials before a commit.
# Exit 0 = clean, safe to commit. Exit 1 = findings present, do not commit.
# Run from anywhere inside the repo: bash .claude/scripts/scan-secrets.sh
set -uo pipefail

STAGED_DIFF=$(git diff --cached --unified=0)
STAGED_FILES=$(git diff --cached --name-only)
FOUND=0

# "regex||description" pairs
PATTERNS=(
  'AKIA[0-9A-Z]{16}||AWS Access Key ID'
  '-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----||Private key block'
  'ghp_[A-Za-z0-9]{36}||GitHub personal access token'
  'gh[oprsu]_[A-Za-z0-9]{36}||GitHub token'
  'xox[baprs]-[A-Za-z0-9-]{10,}||Slack token'
  'sk-[A-Za-z0-9]{20,}||API secret key (sk- prefix)'
  'AIza[0-9A-Za-z_-]{35}||Google API key'
  '(api[_-]?key|secret|password|passwd|token)[[:space:]]*[:=][[:space:]]*.{8,}||Generic hardcoded credential'
  'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}||JWT-looking token'
  # The generic pattern above is case-sensitive and needs the separator right
  # after the keyword, so it misses the shapes below. Values must run to end of
  # line (or close a quote), so references like TOKEN = os.environ.get(...)
  # don't trip them.
  '^\+[[:space:]]*(export[[:space:]]+)?[A-Z0-9_]*(TOKEN|API_?KEY|APIKEY|SECRET|PASSWORD|PASSWD|ACCESS_KEY|PRIVATE_KEY)[A-Z0-9_]*[[:space:]]*=[[:space:]]*["'"'"']?[A-Za-z0-9_+/.=-]{8,}["'"'"']?[[:space:]]*$||Env-style credential assignment (e.g. PLEX_TOKEN_MAIN=...)'
  '["'"'"']([Aa]pi[_-]?[Kk]ey|[Tt]oken|[Ss]ecret|[Pp]assword|[Pp]asswd)["'"'"'][[:space:]]*:[[:space:]]*["'"'"'][^"'"'"'[:space:]]{16,}["'"'"']||Quoted credential key in JSON/YAML'
  '[:=][[:space:]]*["'"'"']?[a-f0-9]{32}["'"'"']?[[:space:]]*([,};)]|$)||32-hex API key (Radarr/Sonarr/Prowlarr/Jellyseerr format)'
  '([Xx]-[Pp]lex-[Tt]oken|[Pp][Ll][Ee][Xx]_?[Tt][Oo][Kk][Ee][Nn][A-Za-z0-9_]*)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_-]{20}||Plex token'
)

ADDED_LINES=$(echo "$STAGED_DIFF" | grep -E '^\+' | grep -Ev '^\+\+\+')

# The generic credential pattern below is broad by design (any
# key/secret/password/token keyword followed by 8+ chars), which also
# catches two common non-secret code shapes: a variable read from the
# environment via a getter call, and an attribute assigned from a
# same-named constructor parameter. Filtering the second shape needs an
# actual identical-both-sides check, not "the right-hand side merely looks
# like an identifier" - that laxer version was tried and rejected during
# review because it also excludes a real, alnum-only quoted secret; don't
# reintroduce a bare-identifier-charset exclusion here.
drop_self_assignments() {
  local out="" line content
  while IFS= read -r line; do
    content="${line#*:}"  # strip the "N:" line-number prefix grep -n adds
    if [[ "$content" =~ ^\+[[:space:]]*(self\.)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*[:=][[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*$ ]] \
       && [ "${BASH_REMATCH[2]}" = "${BASH_REMATCH[3]}" ]; then
      continue  # both sides name the same thing - a reference, not a literal value
    fi
    out+="$line"$'\n'
  done <<< "$1"
  printf '%s' "$out"
}

for entry in "${PATTERNS[@]}"; do
  regex="${entry%%||*}"
  desc="${entry##*||}"
  matches=$(echo "$ADDED_LINES" | grep -En -- "$regex" || true)
  if [ -n "$matches" ] && [ "$desc" = "Generic hardcoded credential" ]; then
    matches=$(echo "$matches" | grep -Ev -- '[:=][[:space:]]*[A-Za-z_][A-Za-z0-9_.]*\(' || true)  # foo = some.call(...)
    [ -n "$matches" ] && matches=$(drop_self_assignments "$matches")
  fi
  if [ -n "$matches" ]; then
    FOUND=1
    echo "BLOCKED: $desc"
    echo "$matches" | sed 's/^/  /'
  fi
done

SENSITIVE_FILE_PATTERNS='(^|/)\.env(\..+)?$|\.pem$|\.key$|\.pfx$|\.p12$|credentials(\.json)?$|secrets?\.ya?ml$|(^|/)id_rsa$|(^|/)id_ed25519$'
# .env.example (and any *.example variant) is the intentional, all-blank
# template meant to be committed - exempt it rather than flag every project
# that follows the documented .env.example convention this repo itself uses.
SENSITIVE_FILES=$(echo "$STAGED_FILES" | grep -E "$SENSITIVE_FILE_PATTERNS" | grep -Ev '\.example$' || true)
if [ -n "$SENSITIVE_FILES" ]; then
  FOUND=1
  echo "BLOCKED: sensitive filenames staged:"
  echo "$SENSITIVE_FILES" | sed 's/^/  /'
fi

if [ "$FOUND" -eq 1 ]; then
  echo ""
  echo "Secret scan FAILED -- do not commit. Resolve findings above (remove the secret, move it to .env, or add the file to .gitignore) and re-run."
  exit 1
fi

echo "Secret scan clean."
exit 0
