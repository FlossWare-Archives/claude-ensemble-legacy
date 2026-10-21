#!/usr/bin/env bash
# Create one validated local annotated MAJOR.MINOR release tag.
# This command does not push tags, create GitHub releases, stage deployments, or activate services.
# Usage: tools/create-ce-release-tag.sh [--repo CHECKOUT] [--deployment-root ROOT]
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
DEPLOYMENT_ROOT=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
usage() {
  printf 'Usage: %s [--repo CHECKOUT] [--deployment-root ROOT]\n' "$0" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || usage
      REPO="$2"
      shift 2
      ;;
    --deployment-root)
      (($# >= 2)) || usage
      DEPLOYMENT_ROOT="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -d "$REPO" ]] || die "repository directory does not exist: $REPO"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a Git checkout: $REPO"
branch="$(git -C "$REPO" branch --show-current)"
[[ "$branch" == "main" ]] || die "release tags may only be created from the main branch (current branch: ${branch:-detached HEAD})"
[[ -z "$(git -C "$REPO" status --porcelain --untracked-files=all)" ]] || die "working tree must be clean, including untracked files"
git -C "$REPO" rev-parse --verify HEAD >/dev/null 2>&1 || die "repository has no commit to tag"
upstream="$(git -C "$REPO" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" || die "main must track an upstream branch; fetch and configure the release checkout before tagging"
commit="$(git -C "$REPO" rev-parse HEAD)"
upstream_commit="$(git -C "$REPO" rev-parse "$upstream^{commit}")"
[[ "$commit" == "$upstream_commit" ]] || die "main must exactly match its upstream ($upstream); fetch/pull and reconcile before tagging"

resolver_args=(--repo "$REPO")
if [[ -n "$DEPLOYMENT_ROOT" ]]; then
  resolver_args+=(--deployment-root "$DEPLOYMENT_ROOT")
fi
VERSION="$(bash "$SCRIPT_DIR/resolve-ce-version.sh" "${resolver_args[@]}")"
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || die "resolver returned an invalid release version: $VERSION"
if git -C "$REPO" show-ref --verify --quiet "refs/tags/$VERSION"; then
  die "release tag already exists: $VERSION"
fi

git -C "$REPO" -c tag.gpgSign=false tag -a "$VERSION" -m "Claude Ensemble $VERSION" "$commit"
[[ "$(git -C "$REPO" cat-file -t "refs/tags/$VERSION")" == "tag" ]] || die "created release reference is not an annotated tag"
[[ "$(git -C "$REPO" rev-parse "$VERSION^{commit}")" == "$commit" ]] || die "created tag does not point to the validated HEAD commit"

printf 'Release tag created: %s\n' "$VERSION"
printf 'Commit: %s\n' "$commit"
printf 'Tag is local only. No remote push, GitHub release, deployment staging, or service activation was performed.\n'
