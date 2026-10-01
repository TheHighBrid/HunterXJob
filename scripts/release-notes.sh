#!/usr/bin/env bash
# Print release notes for a tag: the matching CHANGELOG.md section (if any),
# then the merged pull requests and commits since the previous tag.
# Usage: scripts/release-notes.sh v0.6.0
set -euo pipefail

tag="${1:?usage: release-notes.sh <tag>}"
version="${tag#v}"
root="$(git rev-parse --show-toplevel)"

previous="$(git describe --tags --abbrev=0 --match 'v*' "${tag}^" 2>/dev/null || true)"
range="${previous:+${previous}..}${tag}"

echo "# HunterXJob ${tag}"
echo
if [ -f "${root}/CHANGELOG.md" ]; then
  # The "## [x.y.z]" section for this version, up to the next "## " heading.
  section="$(awk -v v="${version}" '
    /^## / { if (found) exit; if (index($0, "[" v "]") > 0) { found = 1; next } }
    found { print }
  ' "${root}/CHANGELOG.md")"
  if [ -n "${section//[[:space:]]/}" ]; then
    printf '%s\n\n' "${section}"
  fi
fi

echo "## Changes since ${previous:-the first commit}"
echo
merges="$(git log --merges --format='%s%x09%b' "${range}" | awk -F'\t' '
  /^Merge pull request #/ { split($1, a, " "); title = $2; sub(/\n.*/, "", title); print "- " title " (" a[4] ")" }
')"
if [ -n "${merges}" ]; then
  echo "${merges}"
else
  git log --no-merges --format='- %s (%h)' "${range}"
fi
echo
echo "Live submission stays locked in every release; see docs/ARCHITECTURE.md (Safety invariants)."
