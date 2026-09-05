import re
from pathlib import Path


def test_release_version_and_install_rollback_contract():
    version = Path("VERSION").read_text().strip()
    app = Path("app/__init__.py").read_text(encoding="utf-8")
    installer = Path("scripts/marzban.sh").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release-vnext.yml").read_text()
    assert version == "5.2.0"
    assert f'__version__ = "{version}"' in app
    assert f'CLI_RELEASE_VERSION="v{version}"' in installer
    assert f"ghcr.io/smorad3363/marzban-vnext:v{version}" in Path("docker-compose.yml").read_text()
    assert 'MARZBAN_GITHUB_REPO="${MARZBAN_GITHUB_REPO:-smorad3363/Marzban-vNext}"' in installer
    assert 'MARZBAN_GITHUB_BRANCH="${MARZBAN_GITHUB_BRANCH:-vnext-ui}"' in installer
    assert "Application downgrade refused" in installer
    assert "automatic image rollback is unsafe after migrations" in installer
    assert 'flock -n 9' in installer
    assert "Pre-update recovery snapshot" in installer
    assert 'rm -rf "$backup_dir"' not in installer
    assert 'verify_version_integrity "$marzban_version"' in installer
    assert 'verify_version_integrity "$requested_version"' in installer
    assert 'marzban_version="latest"' in installer
    assert 'requested_version="latest"' in installer
    assert 'install_marzban_script_from_repo "$marzban_version" "$MARZBAN_GITHUB_BRANCH"' in installer
    assert 'update_marzban_script "$requested_version" "$MARZBAN_GITHUB_BRANCH"' in installer
    assert 'docker build --pull --tag "$image" "$source_dir"' in installer
    assert 'script_ref_path="refs/heads/${script_ref}"' in installer
    assert 'Configured MySQL image:' in installer
    assert 'Runtime MySQL version:' in installer
    assert 'Refuse overwriting an existing release image' in workflow
    assert 'org.opencontainers.image.revision' in workflow
    assert ':latest' not in workflow
    assert 'gh release create' not in workflow  # Publish notes only after runtime verification.
    assert 'readFileSync("../../VERSION", "utf8").trim()' in Path("app/dashboard/vite.config.ts").read_text()
