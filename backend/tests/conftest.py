"""Isolate unit tests before application modules create stores or read secrets."""
import tempfile

import pytest


def pytest_configure(config: pytest.Config) -> None:
    temporary = tempfile.TemporaryDirectory(prefix="feishu-unit-tests-")
    overrides = pytest.MonkeyPatch()
    overrides.setenv("FEISHU_CLI_DATA_DIR", temporary.name)
    from app.config import Settings, get_settings

    overrides.setitem(Settings.model_config, "env_file", None)
    for key in Settings.model_fields:
        overrides.delenv(key, raising=False)
    get_settings.cache_clear()
    config.add_cleanup(temporary.cleanup)
    config.add_cleanup(overrides.undo)
    config.add_cleanup(get_settings.cache_clear)
