"""Mode and base-URL resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

import ramen_robotics.gate.config as gate_config
from ramen_robotics.gate import GateConfig, GateConfigError, load_env, resolve_base_url, resolve_config


def test_key_wins_over_trial() -> None:
    config = resolve_config(trial=True, environ={"RAMEN_API_KEY": "  abcd1234  "})
    assert config.mode == "keyed" and config.api_key == "abcd1234" and config.trial_ignored
    assert config.min_interval_s == 0.0


def test_trial_without_key() -> None:
    config = resolve_config(trial=True, environ={"RAMEN_API_KEY": "  "})
    assert config.mode == "trial" and config.api_key is None and config.min_interval_s == 2.0


def test_local_without_key_or_trial() -> None:
    assert resolve_config(environ={}).mode == "local"


def test_defaults() -> None:
    config = resolve_config(environ={})
    assert config.timeout_s == 10.0 and config.deadline_s is None
    assert config.base_url == "https://api.ramenai.dev"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "https://api.ramenai.dev"),
        ("https://api.ramenai.dev/", "https://api.ramenai.dev"),
        ("https://EU.ramenai.dev", "https://eu.ramenai.dev"),
    ],
)
def test_canonical_hosts(value: str | None, expected: str) -> None:
    assert resolve_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://api.ramenai.dev",
        "https://",
        "https://user:pw@api.ramenai.dev",
        "https://api.ramenai.dev/v1",
        "https://api.ramenai.dev?x=1",
        "ftp://api.ramenai.dev",
    ],
)
def test_malformed_urls_are_rejected(value: str) -> None:
    with pytest.raises(GateConfigError):
        resolve_base_url(value, allow_custom=True)


def test_custom_host_needs_opt_in() -> None:
    with pytest.raises(GateConfigError, match="RAMEN_ALLOW_CUSTOM_BASE_URL"):
        resolve_config(environ={"RAMEN_BASE_URL": "https://gate.example.com"})
    config = resolve_config(
        environ={"RAMEN_BASE_URL": "https://gate.example.com", "RAMEN_ALLOW_CUSTOM_BASE_URL": "1"}
    )
    assert config.base_url == "https://gate.example.com"
    assert resolve_config(base_url="https://gate.example.com", allow_custom_base_url=True, environ={})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "keyed"},
        {"mode": "trial", "api_key": "abcd1234"},
        {"mode": "local", "timeout_s": 0},
        {"mode": "local", "timeout_s": float("nan")},
        {"mode": "local", "deadline_s": -1.0},
        {"mode": "local", "min_interval_s": -1.0},
        {"mode": "remote"},
    ],
)
def test_invalid_configs(kwargs: dict[str, object]) -> None:
    with pytest.raises(GateConfigError):
        GateConfig(**kwargs)  # type: ignore[arg-type]


def _env_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_env_file_supplies_key_and_base_url(tmp_path: Path) -> None:
    path = _env_file(
        tmp_path,
        '# local secrets\nexport RAMEN_API_KEY="<from-file>"\nRAMEN_BASE_URL=https://eu.ramenai.dev\n',
    )
    config = resolve_config(environ={}, env_file=path)
    assert config.mode == "keyed" and config.api_key == "<from-file>"
    assert config.base_url == "https://eu.ramenai.dev" and config.env_file == str(path)
    assert "<from-file>" not in repr(config)


def test_process_environment_wins_over_env_file(tmp_path: Path) -> None:
    path = _env_file(tmp_path, "RAMEN_API_KEY=from-file\nRAMEN_BASE_URL=https://eu.ramenai.dev\n")
    config = resolve_config(environ={"RAMEN_API_KEY": "from-shell"}, env_file=path)
    assert config.api_key == "from-shell" and config.base_url == "https://eu.ramenai.dev"


def test_default_env_file_read_only_from_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _env_file(tmp_path, "RAMEN_API_KEY=from-file\n")
    monkeypatch.setattr(gate_config, "DEFAULT_ENV_FILE", str(path))
    config = resolve_config()
    assert config.mode == "keyed" and config.api_key == "from-file" and config.env_file == str(path)
    assert resolve_config(environ={}).mode == "local"  # an injected environment stays hermetic


def test_missing_default_env_file_is_fine() -> None:
    config = resolve_config()
    assert config.mode == "local" and config.env_file is None


def test_explicit_env_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError, match="env file not found"):
        resolve_config(environ={}, env_file=tmp_path / "nope.env")


def test_env_file_never_touches_os_environ(tmp_path: Path) -> None:
    import os

    path = _env_file(tmp_path, "RAMEN_API_KEY=from-file\nKEY_WITHOUT_VALUE\n")
    merged, loaded = load_env(environ={}, env_file=path)
    assert merged == {"RAMEN_API_KEY": "from-file"} and loaded == str(path)
    assert "RAMEN_API_KEY" not in os.environ


def test_env_file_custom_host_still_needs_opt_in(tmp_path: Path) -> None:
    path = _env_file(tmp_path, "RAMEN_BASE_URL=https://gate.example.com\n")
    with pytest.raises(GateConfigError, match="RAMEN_ALLOW_CUSTOM_BASE_URL"):
        resolve_config(environ={}, env_file=path)
