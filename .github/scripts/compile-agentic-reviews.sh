#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
gh_aw_command=(gh aw)
if [[ -n "${GH_AW_BIN:-}" ]]; then
  gh_aw_command=("$GH_AW_BIN")
fi

if [[ "$("${gh_aw_command[@]}" --version)" != 'gh aw version v1.0.1' ]]; then
  echo 'Install gh-aw v1.0.1 before regenerating the review workflows.' >&2
  exit 1
fi

"${gh_aw_command[@]}" compile agentic-review-copilot agentic-review-codex --no-check-update --no-logo

# Normalise compiler-generated diagnostics to the repository punctuation convention.
python3 - <<'PY'
from pathlib import Path

for engine in ('copilot', 'codex'):
    target = Path(f'.github/workflows/agentic-review-{engine}.lock.yml')
    target.write_text(target.read_text().replace('\u2014', ':'))

attributes = Path('.gitattributes')
attributes.write_text(attributes.read_text().rstrip('\n') + '\n')
PY
