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
git push origin "HEAD:${BRANCH}"
echo "Committed generated Terraform files."
