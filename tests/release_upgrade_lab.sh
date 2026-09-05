#!/usr/bin/env bash
set -euo pipefail
test "${RELEASE_DISPOSABLE_LAB:-}" = 1
test "$(realpath /opt/marzban)" = /opt/marzban
test "$(realpath /var/lib/marzban)" = /var/lib/marzban
test ! -e /opt/marzban-fresh-evidence
test ! -e /var/lib/marzban-fresh-evidence
docker compose -f /opt/marzban/docker-compose.yml -p marzban down
mv /opt/marzban /opt/marzban-fresh-evidence
mv /var/lib/marzban /var/lib/marzban-fresh-evidence
mkdir -p /opt/marzban /var/lib/marzban
cp /opt/marzban-fresh-evidence/.env /opt/marzban/.env
cp /opt/marzban-fresh-evidence/docker-compose.yml /opt/marzban/docker-compose.yml
cp /var/lib/marzban-fresh-evidence/xray_config.json /var/lib/marzban/xray_config.json
yq -i '.services.marzban.image = "ghcr.io/smorad3363/marzban:v5.1.0" | .services.mysql.image = "mysql:8.0.46" | .services.mysql.volumes = ["/var/lib/marzban/mysql:/var/lib/mysql"]' /opt/marzban/docker-compose.yml
docker compose -f /opt/marzban/docker-compose.yml -p marzban up -d mysql marzban
for attempt in $(seq 1 90); do
  if docker exec marzban-marzban-1 python /code/scripts/healthcheck.py --mode internal --timeout 2 >/dev/null 2>&1; then break; fi
  test "$attempt" -lt 90
  sleep 2
done
docker exec marzban-marzban-1 python -c 'from app import __version__; assert __version__ == "5.1.0"; print("BASELINE_RUNTIME", __version__)'
docker exec marzban-marzban-1 python /code/marzban-cli.py admin bootstrap-owner --username upgrade_owner --password Upgrade-Disposable-Only-927
docker exec marzban-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot "$MYSQL_DATABASE" -e "CREATE TABLE release_upgrade_sentinel(id INT PRIMARY KEY, value VARCHAR(64)); INSERT INTO release_upgrade_sentinel VALUES (1, '\''preserved-through-upgrade'\'');"'
export PATH="/fixtures/bin:$PATH" TERM=xterm MARZBAN_DOCKER_IMAGE=localhost:5000/marzban
bash /fixtures/marzban.sh update --version v5.2.0
bash /usr/local/bin/marzban version
docker exec marzban-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot "$MYSQL_DATABASE" -N -e "SELECT value FROM release_upgrade_sentinel WHERE id=1; SELECT username FROM admins WHERE username='\''upgrade_owner'\''; SELECT version_num FROM alembic_version;"'
test -d /var/lib/marzban/mysql
test -s /opt/marzban/.mysql-migration/state
grep -q 'phase=COMPLETE' /opt/marzban/.mysql-migration/state
test -s /opt/marzban/backup/mysql-migration-*/marzban.sql
echo UPGRADE_V510_TO_V520_PASS
