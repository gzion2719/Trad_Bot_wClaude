"""Section 10: Config validation tests — no IBKR connection needed."""

import pytest

from config.validator import ConfigError, validate_config


def test_cfg01_default_paper_settings_pass():
    validate_config()  # must not raise


def test_cfg02_invalid_port_raises():
    import config.validator as v

    orig = v.IB_PORT
    v.IB_PORT = 9999
    try:
        with pytest.raises(ConfigError):
            validate_config()
    finally:
        v.IB_PORT = orig


def test_cfg03_empty_host_raises():
    import config.validator as v

    orig = v.IB_HOST
    v.IB_HOST = ""
    try:
        with pytest.raises(ConfigError):
            validate_config()
    finally:
        v.IB_HOST = orig
