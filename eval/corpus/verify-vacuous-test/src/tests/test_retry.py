from retry import retry


def test_returns_the_first_success():
    assert retry(lambda: "ok") == "ok"
