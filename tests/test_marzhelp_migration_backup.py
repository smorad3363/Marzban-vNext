import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect

import app.utils.crypto
import config as app_config


REQUIRED_TABLES = {
    "admin_hierarchy",
    "admin_hierarchy_settings",
    "admin_roles",
    "admin_audit_logs",
    "marzhelp_metadata",
    "marzhelp_admin_settings",
    "marzhelp_user_states",
    "marzhelp_user_temporaries",
    "marzhelp_admin_usage",
    "marzhelp_limits",
    "marzhelp_runtime_settings",
    "marzhelp_deleted_users",
    "marzhelp_accounting_transactions",
    "marzhelp_admin_allowed_inbounds",
    "marzhelp_admin_allowed_user_limits",
    "system_owner",
}


def alembic_config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


@pytest.mark.skip(
    reason="production migration evidence is MySQL 8/InnoDB only; the current chain uses MySQL foreign-key DDL"
)
def test_fresh_and_existing_migration_preserve_data(tmp_path, monkeypatch):
    monkeypatch.setattr(
        app.utils.crypto,
        "generate_certificate",
        lambda: {"key": "test-key", "cert": "test-cert"},
    )
    database = tmp_path / "migration.sqlite3"
    monkeypatch.setattr(app_config, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{database}")
    config = alembic_config(database)
    command.upgrade(config, "63fbd07b9f14")
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO admins (username, hashed_password, is_sudo, users_usage) VALUES (?, ?, ?, ?)",
        ("preserved-admin", "hash", 0, 0),
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT username FROM admins").fetchone() == ("preserved-admin",)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert REQUIRED_TABLES <= tables
    marker = dict(connection.execute("SELECT key, value FROM marzhelp_metadata"))
    assert marker["source_id"] == "smorad3363-marzban"
    assert marker["schema_version"] == "1"
    assert connection.execute(
        "SELECT id, code FROM admin_roles ORDER BY id"
    ).fetchall() == [(1, "OWNER"), (2, "SUPER_ADMIN"), (3, "ADMIN")]
    assert connection.execute(
        "SELECT enabled, max_depth FROM admin_hierarchy_settings WHERE id = 1"
    ).fetchone() == (0, 64)
    assert connection.execute("SELECT COUNT(*) FROM system_owner").fetchone() == (0,)
    assert connection.execute(
        "SELECT role_id, parent_admin_id, external_api_enabled "
        "FROM admins WHERE username = 'preserved-admin'"
    ).fetchone() == (None, None, 0)
    assert connection.execute(
        "SELECT depth FROM admin_hierarchy "
        "WHERE ancestor_id = descendant_id"
    ).fetchall() == [(0,)]
    settings_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(marzhelp_admin_settings)")
    }
    assert "max_users" in settings_columns
    assert "capacity_used" in settings_columns
    assert "all_inbounds" in settings_columns
    assert "all_user_limits" in settings_columns
    user_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(users)")
    }
    assert "concurrent_user_limit" in user_columns
    user_indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(users)")
    }
    assert "ix_users_admin_id" in user_indexes
    audit_indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(admin_audit_logs)")
    }
    assert "ix_admin_audit_logs_created_at" in audit_indexes
    connection.close()


def test_sqlite_backup_contains_and_restores_marzhelp_data(tmp_path):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE admins (id INTEGER PRIMARY KEY, username TEXT)")
    connection.execute(
        "CREATE TABLE marzhelp_admin_settings (admin_id INTEGER PRIMARY KEY, user_limit INTEGER)"
    )
    connection.execute("INSERT INTO admins VALUES (1, 'backup-admin')")
    connection.execute("INSERT INTO marzhelp_admin_settings VALUES (1, 17)")
    connection.commit()
    connection.backup(sqlite3.connect(backup))
    connection.close()

    source_backup = sqlite3.connect(backup)
    restored_connection = sqlite3.connect(restored)
    source_backup.backup(restored_connection)
    source_backup.close()
    assert restored_connection.execute(
        "SELECT admin_id, user_limit FROM marzhelp_admin_settings"
    ).fetchone() == (1, 17)
    restored_connection.close()

    backup_script = Path("scripts/marzban.sh").read_text(encoding="utf-8")
    assert 'cp "$sqlite_file" "$temp_dir/db_backup.sqlite"' in backup_script
    assert 'mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" marzban' in backup_script


def test_installer_targets_release_image_and_pinned_mysql_image():
    installer = Path("scripts/marzban.sh").read_text(encoding="utf-8")

    assert 'MARZBAN_GITHUB_REPO="${MARZBAN_GITHUB_REPO:-smorad3363/Marzban-vNext}"' in installer
    assert 'MARZBAN_GITHUB_BRANCH="${MARZBAN_GITHUB_BRANCH:-vnext-ui}"' in installer
    assert 'MARZBAN_DOCKER_IMAGE="${MARZBAN_DOCKER_IMAGE:-ghcr.io/smorad3363/marzban}"' in installer
    assert 'database_type="mysql"' in installer
    assert "This Marzban build supports MySQL only" in installer
    assert 'marzban_version="$CLI_RELEASE_VERSION"' in installer
    assert 'elif [ "$database_type" == "mysql" ]; then' in installer
    assert 'image: $(marzban_docker_image "${marzban_version}")' in installer
    assert 'MYSQL_TARGET_IMAGE="mysql:${MYSQL_TARGET_VERSION}"' in installer
    assert "image: ${MYSQL_TARGET_IMAGE}" in installer
    assert "mysql-${MYSQL_TARGET_VERSION}:/var/lib/mysql" in installer
    assert "    image: mysql:8.0\n" not in installer
    assert 'requested_version="$CLI_RELEASE_VERSION"' in installer
    assert 'previous_image=$(yq -r' in installer
    assert "for attempt in $(seq 1 150)" in installer
    assert "/code/scripts/healthcheck.py --mode internal --timeout 3" in installer
    assert 'docker logs --tail 200 "$container_id"' in installer
    assert "Update health check failed" in installer
    assert 'update_command --version "$1"' in installer
    assert 'rollback)' in installer


def test_mysql_upgrade_uses_resumable_logical_dump_restore():
    installer = Path("scripts/marzban.sh").read_text(encoding="utf-8")

    assert 'MYSQL_TARGET_VERSION="26.7.0"' in installer
    assert 'phase="DUMPED"' in installer
    assert 'phase="TARGET_CONFIGURED"' in installer
    assert 'phase="RESTORED"' in installer
    assert 'phase="COMPLETE"' in installer
    assert '--databases "$MYSQL_DATABASE" --single-transaction' in installer
    assert "--set-gtid-purged=OFF" in installer
    assert 'sha256sum "$logical_backup"' in installer
    assert 'sha256sum -c "$logical_backup.sha256"' in installer
    assert 'Original data preserved: ${source_data}' in installer
    assert 'Logical restore failed. Source data remains untouched' in installer
    assert 'mysql-upgrade)' in installer
    assert 'mysql_upgrade_command "$@"' in installer
    auto_upgrade = installer.index("if mysql_upgrade_required_for_update; then")
    app_pull = installer.index("target_image=$(marzban_docker_image", auto_upgrade)
    assert auto_upgrade < app_pull
    assert 'mysql_upgrade_command\n' in installer[auto_upgrade:app_pull]
    assert 'mysql_preflight' in installer
    migration = installer.split("mysql_upgrade_command() {", 1)[1]
    assert migration.index('stop marzban || exit 1') < migration.index('exec mysqldump')
    assert migration.index('mysql_source_version_supported "$source_version"') < migration.index('exec mysqldump')


def test_mysql_downgrade_guard_executes_for_supported_and_unknown_versions():
    import shutil
    import subprocess
    import pytest
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    executable = str(bash) if bash.exists() else shutil.which("bash")
    if not executable:
        pytest.skip("Bash is unavailable")
    source = Path("scripts/marzban.sh").read_text(encoding="utf-8")
    body = source.split("mysql_source_version_supported() {", 1)[1].split("\n}", 1)[0]
    script = 'MYSQL_TARGET_VERSION="26.7.0"\nmysql_source_version_supported() {' + body + '\n}\n'
    script += 'mysql_source_version_supported 8.0.46 && mysql_source_version_supported 26.7.0 && ! mysql_source_version_supported 26.8.0 && ! mysql_source_version_supported unknown'
    result = subprocess.run([executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_version_command_reports_and_enforces_release_integrity():
    installer = Path("scripts/marzban.sh").read_text(encoding="utf-8")

    assert "version_command()" in installer
    assert "verify_version_integrity()" in installer
    assert 'echo "CLI version: ${cli_version}"' in installer
    assert 'echo "Runtime app version: ${runtime_version}"' in installer
    assert 'echo "Configured Docker image: ${configured_image}"' in installer
    assert 'echo "Running Docker image: ${running_image}"' in installer
    assert 'echo "Immutable image digest: ${digest}"' in installer
    assert 'verify_version_integrity "$marzban_version"' in installer
    assert 'verify_version_integrity "$requested_version"' in installer
    assert "version)" in installer
