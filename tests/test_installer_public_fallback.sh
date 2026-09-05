#!/usr/bin/env bash
set -eo pipefail

source scripts/marzban.sh
set -u

help_output=$(TERM=xterm bash -c "$(cat scripts/marzban.sh)" @ help)
grep -q "Usage:" <<< "$help_output"
grep -q "install" <<< "$help_output"

update_help=$(TERM=xterm bash -c "$(cat scripts/marzban.sh)" @ update --help)
grep -q "Usage: marzban update" <<< "$update_help"

resolved="$CLI_RELEASE_VERSION"
mock_revision="2d8df17b526236c9980ade37d802531dbca0d06f"
built="false"

release_commit_for_version() {
    printf '%s\n' "$mock_revision"
}

docker() {
    if [ "$1" = "pull" ]; then
        return 1
    fi
    if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
        if [[ "$*" == *"--format"* ]]; then
            if [ "$built" = "true" ]; then
                printf '%s\n' "$mock_revision"
            else
                printf '%s\n' "stale-cached-revision"
            fi
        fi
        return 0
    fi
    if [ "$1" = "build" ]; then
        local context="${!#}"
        test -f "$context/Dockerfile"
        test -f "$context/VERSION"
        test "$(tr -d '\r\n' < "$context/VERSION")" = "${resolved#v}"
        [[ "$*" == *"org.opencontainers.image.revision=${mock_revision}"* ]]
        built="true"
        printf 'FALLBACK_BUILD_OK\n'
        return 0
    fi
    return 1
}

ensure_marzban_image "$resolved"
