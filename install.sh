#!/usr/bin/env bash
# gistgrep installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mattheworiordan/gistgrep/main/install.sh | bash
#
# Installs to ~/.local/bin/gistgrep (override with INSTALL_DIR env var).

set -euo pipefail

REPO="mattheworiordan/gistgrep"
REF="${GISTGREP_REF:-main}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
URL="https://raw.githubusercontent.com/${REPO}/${REF}/bin/gistgrep"

echo "→ installing gistgrep to ${INSTALL_DIR}"

mkdir -p "$INSTALL_DIR"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if ! curl -fsSL "$URL" -o "$TMP"; then
  echo "✗ failed to download ${URL}" >&2
  exit 1
fi

# Sanity: must look like our script
if ! head -1 "$TMP" | grep -q '^#!/usr/bin/env python3'; then
  echo "✗ downloaded file doesn't look right" >&2
  exit 1
fi

install -m 0755 "$TMP" "$INSTALL_DIR/gistgrep"

echo "✓ installed $(${INSTALL_DIR}/gistgrep --version)"

case ":$PATH:" in
  *":${INSTALL_DIR}:"*) ;;
  *)
    echo
    echo "⚠ ${INSTALL_DIR} isn't on your PATH. Add this to your shell rc:"
    echo "    export PATH=\"${INSTALL_DIR}:\$PATH\""
    ;;
esac

echo
echo "next: run 'gistgrep --doctor' to verify dependencies"
