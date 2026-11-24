#!/usr/bin/env bash
# release.sh X.Y.Z — set the monorepo version in every collection's galaxy.yml,
# commit, and print (not run) the tag command. The whole repo shares one version.
set -euo pipefail

die() { printf 'release.sh: %s\n' "$*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: $0 X.Y.Z"
version="${1#v}"
semver='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$'
[[ "$version" =~ $semver ]] || die "'$1' is not a semantic version (X.Y.Z)"

cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean"
! git rev-parse -q --verify "refs/tags/v$version" >/dev/null || die "tag v$version already exists"
branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" == main ]] || printf 'release.sh: warning: releasing from %s, not main\n' "$branch" >&2

mapfile -t files < <(git ls-files 'ansible_collections/*/*/galaxy.yml')
((${#files[@]})) || die "no galaxy.yml files found"

for f in "${files[@]}"; do
  grep -qE '^version:' "$f" || die "$f has no top-level version: key"
  sed -i -E "s/^version:.*/version: $version/" "$f"
  printf '  %s -> %s\n' "$f" "$version"
done

git add -- "${files[@]}"
if git diff --cached --quiet; then
  echo "All collections already at $version; nothing to commit."
else
  git commit --quiet -m "release: v$version"
  echo "Committed: $(git log -1 --format='%h %s')"
fi

cat <<EOF

Review, then tag and push:

  git tag -a v$version -m "v$version" && git push origin $branch v$version
EOF
