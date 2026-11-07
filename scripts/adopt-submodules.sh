#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# adopt-submodules.sh — turn backend/, frontend/ and ml/ into git submodules of
# the HireSnap superproject.
#
# WHY THIS EXISTS
#   backend/, frontend/ and ml/ are independent checkouts of the same remote
#   (Prischli-Drink-Coffee/HireSnap.git) sitting on different branches
#   (`backend`, `frontend`, `ml`). They must become gitlinks of the root repo so
#   that a single `git clone --recurse-submodules` reproduces the exact triple of
#   commits. Doing that by hand is three commands per service (`submodule add -b`), and
#   the failure mode of getting it wrong is silent: the root repo records the
#   directory as ordinary files, and every later pointer bump is meaningless.
#   Mirrors the layout R:\GPTHub keeps via .gitmodules.
#
# CONTRACT
#   * Idempotent. Running it twice changes nothing: an already-adopted path is
#     detected from .gitmodules and from the index (gitlink mode 160000) and is
#     reported, not re-added.
#   * `--check` (aka `--dry-run`) prints the exact plan and exits without touching
#     git. Use it in CI and before a first run on someone else's machine.
#   * It never commits. Adoption rewrites .gitmodules and stages the gitlinks;
#     you review and commit that yourself.
#   * It never rewrites the submodules' history, remotes, or branches. The
#     submodule keeps its remote and its checked-out branch; only the root repo
#     records `branch = <name>` so that `git submodule update --remote` follows
#     the right line of development.
#
# USAGE
#   bash scripts/adopt-submodules.sh --check   # show the plan, change nothing
#   bash scripts/adopt-submodules.sh           # adopt
#   bash scripts/adopt-submodules.sh --force   # adopt even with a dirty submodule
#
# On Windows the executable bit is not stored by git; invoke it as
# `bash scripts/adopt-submodules.sh` rather than `./scripts/adopt-submodules.sh`.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Paths and defaults ───────────────────────────────────────────────────────
# Root resolves from this script's location (scripts/../), so the helper works
# from any cwd. HIRESNAP_ROOT overrides it — used by the self-test and by anyone
# adopting a fork in a non-standard checkout.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${HIRESNAP_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# path:branch — the branch is the one the standalone checkout lives on, and it
# is also the name recorded in .gitmodules. Keep the two in sync: a mismatch is
# exactly the bug (`submodule update --remote` pulling the wrong line) that the
# `branch =` key exists to prevent.
SUBMODULES=(
  "backend:backend"
  "frontend:frontend"
  "ml:ml"
)

CHECK_ONLY=0
FORCE=0

usage() {
  sed -n '3,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--dry-run|-n) CHECK_ONLY=1 ;;
    --force|-f)           FORCE=1 ;;
    --root)               shift; ROOT="$1" ;;
    -h|--help)            usage ;;
    *) printf 'adopt-submodules: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# ── Output helpers ───────────────────────────────────────────────────────────
# Every planned mutation is printed with the RUN/PLAN prefix, so a --check run is
# literally the transcript of what a real run would do. Nothing mutating is
# printed without that prefix.
say()  { printf '%s\n' "$*"; }
info() { printf '[info] %s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die()  { printf '[error] %s\n' "$*" >&2; exit 1; }
step() { if [[ $CHECK_ONLY -eq 1 ]]; then printf 'PLAN: %s\n' "$*"; else printf 'RUN:  %s\n' "$*"; fi; }

# Runs a mutating command, or prints it when --check is set.
run() {
  if [[ $CHECK_ONLY -eq 1 ]]; then
    step "$*"
    return 0
  fi
  step "$*"
  "$@"
}

# ── Preconditions on the superproject ────────────────────────────────────────
[[ -d "$ROOT" ]] || die "root directory does not exist: $ROOT"
git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 \
  || die "$ROOT is not a git repository — run 'git init -b main' there first"

if [[ $CHECK_ONLY -eq 0 ]] && ! git -C "$ROOT" rev-parse --verify -q HEAD >/dev/null; then
  # Not fatal: git can stage a gitlink into an unborn branch. It is called out
  # because the first commit then contains only .gitmodules and the pointers,
  # which surprises people reviewing the diff.
  warn "root repository has no commits yet; the adoption will be part of the first commit"
fi

say "HireSnap superproject: $ROOT"
say "mode: $([[ $CHECK_ONLY -eq 1 ]] && echo 'check (no changes)' || echo 'adopt')$([[ $FORCE -eq 1 ]] && echo ', forced')"
say ""

# ── Detection helpers ────────────────────────────────────────────────────────
# True when <path> is registered in .gitmodules.
in_gitmodules() {
  local path="$1"
  [[ -f "$ROOT/.gitmodules" ]] || return 1
  git -C "$ROOT" config -f .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null \
    | awk '{print $2}' | grep -Fxq -- "$path"
}

# True when the index records <path> as a gitlink (mode 160000) rather than as
# ordinary tracked files. This is the authoritative test: .gitmodules can be
# hand-edited, the index cannot lie about what the root commit will contain.
is_gitlink() {
  local path="$1"
  git -C "$ROOT" ls-files -s -- "$path" 2>/dev/null | grep -q '^160000 '
}

# True when the path is tracked as plain files — the state adoption must undo
# before the gitlink can be staged.
tracked_as_files() {
  local path="$1"
  local modes
  modes="$(git -C "$ROOT" ls-files -s -- "$path" 2>/dev/null | awk '{print $1}' | sort -u || true)"
  [[ -n "$modes" ]] && ! grep -qx '160000' <<<"$modes"
}

ADOPTED=0
SKIPPED=0
FAILED=0

for entry in "${SUBMODULES[@]}"; do
  path="${entry%%:*}"
  branch="${entry##*:}"
  dir="$ROOT/$path"

  say "── $path ──────────────────────────────────────────────────────────"

  # 1. Already a submodule? Report the recorded commit and move on. This is the
  #    idempotency branch: a second run must be a no-op, not an error.
  if in_gitmodules "$path" && is_gitlink "$path"; then
    recorded="$(git -C "$ROOT" ls-tree HEAD -- "$path" 2>/dev/null | awk '{print $3}' || true)"
    info "already adopted (gitlink ${recorded:-not yet committed}) — nothing to do"
    if [[ ! -d "$dir/.git" && ! -f "$dir/.git" ]]; then
      warn "working tree is empty; run: git submodule update --init -- $path"
    fi
    SKIPPED=$((SKIPPED + 1))
    say ""
    continue
  fi

  # 2. Half-adopted state is a real failure, not something to paper over:
  #    .gitmodules names the path but the index disagrees.
  if in_gitmodules "$path" && ! is_gitlink "$path"; then
    warn "$path is listed in .gitmodules but not staged as a gitlink."
    warn "Inspect 'git status' and 'git ls-files -s -- $path' before re-running; refusing to guess."
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi

  # 3. The directory must be a real, committed clone.
  if [[ ! -d "$dir" ]]; then
    warn "$path does not exist — clone it first, this script does not invent a repository"
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi
  if ! git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$path exists but is not a git repository"
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi
  if ! git -C "$dir" rev-parse --verify -q HEAD >/dev/null; then
    warn "$path has no commits (unborn branch) — cannot record a gitlink to nothing"
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi

  url="$(git -C "$dir" config --get remote.origin.url || true)"
  if [[ -z "$url" ]]; then
    warn "$path has no 'origin' remote — a submodule entry without a URL cannot be cloned by anyone else"
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi

  current_branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD || true)"
  if [[ "$current_branch" == "HEAD" ]]; then
    warn "$path is on a detached HEAD; check out its branch before adopting"
    FAILED=$((FAILED + 1))
    say ""
    continue
  fi
  if [[ "$current_branch" != "$branch" ]]; then
    # Not fatal — but a wrong branch means the root repo will record a pointer on
    # a line of development nobody expects, so it needs an explicit --force.
    warn "$path is on branch '$current_branch', expected '$branch'"
    if [[ $FORCE -eq 0 ]]; then
      warn "check out '$branch' (or pass --force if '$current_branch' is intended)"
      FAILED=$((FAILED + 1))
      say ""
      continue
    fi
  fi

  # 4. Uncommitted tracked changes would be adopted as "whatever is on disk",
  #    which the gitlink cannot represent: the pointer records HEAD, not the
  #    working tree. Adopting now silently mislabels the state.
  dirty="$(git -C "$dir" status --porcelain --untracked-files=no || true)"
  if [[ -n "$dirty" ]]; then
    warn "$path has uncommitted tracked changes:"
    printf '%s\n' "$dirty" | sed 's/^/         /' >&2
    if [[ $FORCE -eq 0 ]]; then
      warn "commit inside $path first (see AGENTS.md), then re-run; --force adopts HEAD anyway"
      FAILED=$((FAILED + 1))
      say ""
      continue
    fi
    warn "--force: adopting HEAD, the working-tree changes stay uncommitted"
  fi

  commit="$(git -C "$dir" rev-parse HEAD)"
  info "remote:  $url"
  info "branch:  $current_branch"
  info "commit:  $commit"

  # 5. If the root already tracks the path as ordinary files, they must leave the
  #    index first, otherwise 'submodule add' fails with "already exists in the
  #    index" and leaves a half-written .gitmodules behind.
  if tracked_as_files "$path"; then
    warn "$path is currently tracked as ordinary files in the root index"
    # 'rm --cached' only empties the index; the working tree is untouched, so a
    # failure here is safe to abort on. The old file entries are replaced by the
    # gitlink in the same commit, and git will show them as deletions.
    if ! run git -C "$ROOT" rm -r --cached --quiet -- "$path"; then
      warn "could not unstage the ordinary files under $path — resolve manually, then re-run"
      FAILED=$((FAILED + 1))
      say ""
      continue
    fi
  fi

  # 6. Adopt. '-b <branch>' records submodule.<name>.branch so that
  #    'git submodule update --remote' tracks the right branch instead of the
  #    remote HEAD (which here points at 'backend', i.e. the wrong service).
  #
  #    Local-path and file:// remotes are refused by git's transport policy for
  #    submodule clones (CVE-2022-39253 fix). When the origin really is a local
  #    mirror the user owns, allow the file transport for this invocation only —
  #    the exception is scoped to this command, never written to config.
  add_args=(-C "$ROOT" submodule add -b "$branch")
  if [[ "$url" != *://* || "$url" == file://* ]]; then
    info "local-path remote: enabling the file transport for this command only"
    add_args=(-c protocol.file.allow=always "${add_args[@]}")
  fi
  run git "${add_args[@]}" "$url" "$path" || {
    warn "git submodule add failed for $path"
    FAILED=$((FAILED + 1))
    say ""
    continue
  }

  # 7. 'submodule add' writes 'branch' when -b is given; set it again so the file
  #    is correct even if a git version ignores -b (recorded explicitly, cheap).
  run git -C "$ROOT" config -f .gitmodules "submodule.$path.branch" "$branch"

  ADOPTED=$((ADOPTED + 1))
  say ""
done

# ── Summary ──────────────────────────────────────────────────────────────────
say "──────────────────────────────────────────────────────────────────"
say "adopted: $ADOPTED   already-adopted: $SKIPPED   failed: $FAILED"

if [[ $FAILED -gt 0 ]]; then
  say ""
  die "$FAILED submodule(s) need attention — nothing above was committed"
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
  say ""
  say "Dry run only: nothing was changed. Re-run without --check to adopt."
  exit 0
fi

if [[ $ADOPTED -gt 0 ]]; then
  say ""
  say "Next steps (review before committing):"
  say "  git -C $ROOT status"
  say "  git -C $ROOT add .gitmodules"
  say "  git -C $ROOT commit -m 'chore(repo): adopt backend, frontend and ml as submodules'"
  say ""
  say "Never commit .env, keys, or stack data — see .gitignore and AGENTS.md."
fi

exit 0
