#!/bin/sh
# Resolve a tool version from the app repo's own version files.
#
# Usage: resolve-tool-version.sh <tool> <explicit> <default>
#   tool:     node | java
#   explicit: version passed as a workflow input ("" when not set) — wins
#   default:  used when no version file pins the tool
#
# Looks in, in order: .nvmrc / .node-version (node), .java-version (java),
# .tool-versions (asdf), mise.toml / .mise.toml ([tools] section).
#
# Output: version=<value> in GITHUB_OUTPUT (and on stdout).

set -e

TOOL="${1:?Usage: resolve-tool-version.sh <tool> <explicit> <default>}"
EXPLICIT="$2"
DEFAULT="$3"

from_files() {
  case "$TOOL" in
    node)
      for f in .nvmrc .node-version; do
        [ -f "$f" ] && { tr -d ' \r\nv' < "$f"; return; }
      done
      ;;
    java)
      [ -f .java-version ] && { tr -d ' \r\n' < .java-version; return; }
      ;;
  esac

  if [ -f .tool-versions ]; then
    # asdf names Java "java" and node "nodejs"
    v=$(awk -v t="$TOOL" '$1 == t || (t == "node" && $1 == "nodejs") { print $2; exit }' .tool-versions)
    [ -n "$v" ] && { echo "$v"; return; }
  fi

  for f in mise.toml .mise.toml; do
    if [ -f "$f" ]; then
      v=$(awk -v t="$TOOL" '
        /^\[/ { in_tools = ($0 == "[tools]") ; next }
        in_tools && $1 == t { gsub(/["'\'' ]/, "", $3); print $3; exit }
      ' "$f")
      [ -n "$v" ] && { echo "$v"; return; }
    fi
  done
}

VERSION="$EXPLICIT"
[ -z "$VERSION" ] && VERSION=$(from_files)
[ -z "$VERSION" ] && VERSION="$DEFAULT"

# Java distributions take a major version; strip vendor prefixes like
# "temurin-25.0.1" (asdf/mise) down to "25".
if [ "$TOOL" = "java" ]; then
  VERSION=$(echo "$VERSION" | sed -E 's/^[a-z-]*-//; s/^([0-9]+).*/\1/')
fi

echo "version=${VERSION}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "$TOOL version: ${VERSION}"
