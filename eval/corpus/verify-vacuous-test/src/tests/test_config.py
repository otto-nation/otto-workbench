from config import parse_port


def test_parses_a_valid_port():
    assert parse_port("8080") == 8080
