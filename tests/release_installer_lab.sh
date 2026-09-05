#!/usr/bin/env bash
# Run only inside the disposable Docker-in-Docker release lab.
set -euo pipefail
test "${RELEASE_DISPOSABLE_LAB:-}" = "1"
test -f /fixtures/marzban.sh
mkdir -p /fixtures/bin
cat > /fixtures/bin/curl <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
destination=''
url=''
while (($#)); do
  case "$1" in
    -o) destination="$2"; shift 2 ;;
    --header) shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
case "$url" in
  https://api.github.com/repos/*/releases) printf '[{"tag_name":"v5.2.0","draft":false,"prerelease":false}]'; exit ;;
  https://raw.githubusercontent.com/*/scripts/marzban.sh) source_file=/fixtures/marzban.sh ;;
  https://raw.githubusercontent.com/*/.env.example) source_file=/fixtures/.env.example ;;
  https://raw.githubusercontent.com/*/xray_config.json) source_file=/fixtures/xray_config.json ;;
  *) echo "Unexpected fixture request: $url" >&2; exit 1 ;;
esac
test -n "$destination"
cp "$source_file" "$destination"
CURL
chmod 755 /fixtures/bin/curl
export PATH="/fixtures/bin:$PATH" TERM=xterm
export MARZBAN_DOCKER_IMAGE=localhost:5000/marzban
bash /fixtures/marzban.sh help >/tmp/cli-help.txt
grep -q 'mysql-upgrade' /tmp/cli-help.txt
printf '\n' | bash /fixtures/marzban.sh install --version v5.2.0 --database mysql
bash /usr/local/bin/marzban version
bash /usr/local/bin/marzban status
before=$(sha256sum /opt/marzban/.env)
if bash /usr/local/bin/marzban install --version v5.2.0; then
  echo 'Reinstall unexpectedly succeeded' >&2; exit 1
fi
test "$before" = "$(sha256sum /opt/marzban/.env)"
if bash /usr/local/bin/marzban rollback v5.1.0; then
  echo 'Downgrade unexpectedly succeeded' >&2; exit 1
fi
echo 'FRESH_INSTALL_AND_DISPATCH_PASS'
