#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
	LINK_TARGET="$(readlink "$SCRIPT_PATH")"
	if [[ "$LINK_TARGET" == /* ]]; then
		SCRIPT_PATH="$LINK_TARGET"
	else
		SCRIPT_PATH="$(dirname "$SCRIPT_PATH")/$LINK_TARGET"
	fi
done

SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

exec "$SCRIPT_DIR/pi-test.sh" "$@"
