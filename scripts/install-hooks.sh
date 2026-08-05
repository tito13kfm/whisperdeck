#!/bin/sh
# Install this repo's git hooks. Run once per clone:
#
#     sh scripts/install-hooks.sh
#
# Copies .githooks/* into .git/hooks/ rather than pointing core.hooksPath at
# .githooks, and that distinction is the whole reason this script exists.
#
# core.hooksPath is resolved relative to the WORKING TREE. The post-checkout
# hook's job is to warn when the main checkout is moved onto an old branch, and
# an old branch does not contain .githooks/ -- so with core.hooksPath the hook
# file is missing at exactly the moment it is needed, and the checkout happens
# in silence. Verified: with core.hooksPath set, checking out a branch that
# predates the hook produces no warning at all.
#
# .git/hooks lives in the common git dir. It is shared by every linked worktree
# and is unaffected by which branch any of them has checked out, so the hook
# fires regardless. That is what this script sets up.
set -e

repo_common=$(git rev-parse --path-format=absolute --git-common-dir)
src_root=$(git rev-parse --show-toplevel)
hooks_src="$src_root/.githooks"
hooks_dst="$repo_common/hooks"

if [ ! -d "$hooks_src" ]; then
    echo "No .githooks/ in $src_root. Are you on a branch that predates it?" >&2
    exit 1
fi

mkdir -p "$hooks_dst"

# core.hooksPath, if set, makes git ignore .git/hooks entirely. Clear it so the
# copies below are the ones that actually run.
if git config --get core.hooksPath >/dev/null 2>&1; then
    echo "unsetting core.hooksPath (it shadows .git/hooks)"
    git config --unset-all core.hooksPath
fi

for hook in "$hooks_src"/*; do
    [ -f "$hook" ] || continue
    name=$(basename "$hook")
    cp "$hook" "$hooks_dst/$name"
    chmod +x "$hooks_dst/$name"
    echo "installed $name -> $hooks_dst/$name"
done

echo "done. Hooks are shared by every worktree of this repo."
