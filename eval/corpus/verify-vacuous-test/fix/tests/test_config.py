import pytest

from config import parse_port


def test_parses_a_valid_port():
    assert parse_port("8080") == 8080


def test_port_range():
    for raw in ("0", "70000"):
        with pytest.raises(ValueError):
            parse_port(raw)
