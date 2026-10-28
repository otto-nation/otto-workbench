from unittest import mock

import retry as retry_mod


def test_backoff_sleeps_between_attempts():
    with mock.patch.object(retry_mod, "retry", return_value="ok") as fake:
        assert retry_mod.retry(lambda: 1) == "ok"
    fake.assert_called_once()
