"""Settings are loaded from COHORTSPLIT_* env vars and never leak secrets."""

import pytest
from pydantic import SecretStr

from cohortsplit.config import ConfigError, load_settings
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


def test_loads_settings_from_prefixed_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_HOST", "db.internal")
    clean_env.setenv("COHORTSPLIT_APPDB_PORT", "6543")
    clean_env.setenv("COHORTSPLIT_APPDB_NAME", "meta")
    clean_env.setenv("COHORTSPLIT_APPDB_USER", "meta_user")
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv(
        "COHORTSPLIT_WAREHOUSE_DSN",
        f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@wh:5432/ecommerce",
    )

    settings = load_settings()

    assert settings.appdb_host == "db.internal"
    assert settings.appdb_port == 6543
    assert settings.appdb_name == "meta"
    assert settings.appdb_user == "meta_user"
    assert isinstance(settings.appdb_password, SecretStr)
    assert settings.appdb_password.get_secret_value() == APPDB_PASSWORD
    assert isinstance(settings.warehouse_dsn, SecretStr)
    assert WAREHOUSE_PASSWORD in settings.warehouse_dsn.get_secret_value()


def test_defaults_apply_when_only_required_settings_given(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)

    settings = load_settings()

    assert settings.appdb_host == "localhost"
    assert settings.appdb_port == 5432
    assert settings.warehouse_dsn is None
    assert settings.frontend_dist is None


def test_missing_required_setting_error_names_the_env_var(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="COHORTSPLIT_APPDB_PASSWORD"):
        load_settings()


def test_empty_required_secret_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", "")

    with pytest.raises(ConfigError, match="COHORTSPLIT_APPDB_PASSWORD"):
        load_settings()


def test_unprefixed_env_var_is_not_used(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("APPDB_PASSWORD", APPDB_PASSWORD)

    with pytest.raises(ConfigError, match="COHORTSPLIT_APPDB_PASSWORD"):
        load_settings()


def test_invalid_setting_error_names_the_env_var(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv("COHORTSPLIT_APPDB_PORT", "not-a-port")

    with pytest.raises(ConfigError, match="COHORTSPLIT_APPDB_PORT"):
        load_settings()


def test_config_error_does_not_echo_secret_values(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv("COHORTSPLIT_APPDB_PORT", WAREHOUSE_PASSWORD)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    assert WAREHOUSE_PASSWORD not in str(excinfo.value)
    assert APPDB_PASSWORD not in str(excinfo.value)


def test_secrets_never_appear_in_repr_or_str(unreachable_appdb_env: pytest.MonkeyPatch) -> None:
    settings = load_settings()

    for rendered in (repr(settings), str(settings)):
        assert APPDB_PASSWORD not in rendered
        assert WAREHOUSE_PASSWORD not in rendered
