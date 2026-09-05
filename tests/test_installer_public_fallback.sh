#!/usr/bin/env bash
set -eo pipefail

source scripts/marzban.sh
set -u

help_output=$(TERM=xterm bash -c "$(cat scripts/marzban.sh)" @ help)
grep -q "Usage:" <<< "$help_output"
grep -q "install" <<< "$help_output"

resolved="$CLI_RELEASE_VERSION"

docker() {
    if [ "$1" = "image" ] || [ "$1" = "pull" ]; then
        return 1
    fi
    if [ "$1" = "build" ]; then
        local context="${!#}"
        test -f "$context/Dockerfile"
        test -f "$context/VERSION"
        test "$(tr -d '\r\n' < "$context/VERSION")" = "${resolved#v}"
        printf 'FALLBACK_BUILD_OK\n'
        return 0
    fi
    return 1
}

ensure_marzban_image "$resolved"
