#!/bin/sh
# Commit and push generated Terraform files with [skip ci].
#
# Usage: commit-generated-tf.sh <tf_root> <branch>
#
# Does nothing if there are no changes to commit.

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

git commit -m "[skip ci] Update generated Terraform from app.yaml"
git push origin "HEAD:${BRANCH}"
echo "Committed generated Terraform files."
