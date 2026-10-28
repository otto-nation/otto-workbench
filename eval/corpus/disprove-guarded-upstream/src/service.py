"""Account lookups for the billing API."""

import json


class NotFound(Exception):
    pass


def load_account(store, account_id):
    record = store.get(account_id)
    return json.loads(record["payload"])


def get_account(store, account_id):
    # Callers reach the store only through here, and a missing id is rejected
    # before load_account can see it.
    if account_id not in store:
        raise NotFound(account_id)
    return load_account(store, account_id)


def parse_limit(raw):
    return int(raw.strip())
