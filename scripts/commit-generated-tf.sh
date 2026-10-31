#!/bin/sh
# Commit and push generated Terraform files.
#
# No [skip ci]: the push uses the caller's GITHUB_TOKEN, which never starts
# workflows, and a [skip ci] in the message ends up in squash-merge commits,
# silently skipping the deploy of the merged PR.
#
# Usage: commit-generated-tf.sh <tf_root> <branch>
#
# Does nothing if there are no changes to commit.
# Env: FORK_PR=true skips the push — a fork PR's token is read-only. tf-plan
#      on that PR then plans whatever Terraform is committed on the branch.
#
# A rejected push fails the job with the reason and the fix: branch
# protection or a ruleset on the branch, a read-only token, or a branch that
# moved while the files were generated.

set -e

TF_ROOT="$1"
BRANCH="$2"

git config user.name "javabin-platform[bot]"
git config user.email "platform@javazone.no"

git add "$TF_ROOT/"

if git diff --cached --quiet; then
  echo "Generated Terraform files are already up to date."
  exit 0
fi

if [ "${FORK_PR:-false}" = "true" ]; then
  echo "::warning::Generated Terraform differs from the committed files, but this PR comes from a fork and cannot be pushed to. It will be regenerated after merge."
  git diff --cached --stat
  exit 0
fi

git commit -m "chore(terraform): update generated Terraform from app.yaml"

if PUSH_OUTPUT=$(git push origin "HEAD:${BRANCH}" 2>&1); then
  echo "$PUSH_OUTPUT"
  echo "Committed generated Terraform files."
  if [ -n "${GITHUB_HEAD_REF:-}" ]; then
    echo "::notice title=Generated Terraform pushed::Pushed $(git rev-parse --short HEAD) to ${BRANCH}. GitHub starts no workflow for pushes made with GITHUB_TOKEN, so checks do not run on that commit. If the repo requires status checks, push another commit to the PR (e.g. git commit --allow-empty) after pulling it."
  fi
  exit 0
fi

echo "$PUSH_OUTPUT"
git diff --stat HEAD~1 HEAD

case "$PUSH_OUTPUT" in
  *GH006*|*GH013*|*"protected branch"*|*"Protected branch"*|*"rule violations"*)
    echo "::error title=Generated Terraform could not be pushed::${BRANCH} is protected (branch protection or a ruleset), so the generated Terraform for app.yaml cannot be pushed to it. Change app.yaml through a pull request: expand commits the generated files to the PR branch, and ${BRANCH} is up to date after merge. If ${BRANCH} is already out of date (for example after a platform generator change), open a PR from ${BRANCH} with any change and merge it."
    ;;
  *"non-fast-forward"*|*"fetch first"*|*"[rejected]"*)
    echo "::error title=Generated Terraform could not be pushed::${BRANCH} moved while this run generated Terraform. The run for the newer commit regenerates and pushes it; re-run this workflow if that run does not exist."
    ;;
  *403*|*"Permission to"*|*"denied"*|*"write access"*)
    echo "::error title=Generated Terraform could not be pushed::The GITHUB_TOKEN of this run cannot push to ${BRANCH}. Give the workflow 'contents: write' in deploy.yml. Dependabot PRs get a read-only token unless deploy.yml grants it."
    ;;
  *)
    echo "::error title=Generated Terraform could not be pushed::git push to ${BRANCH} failed; see the output above."
    ;;
esac
exit 1
