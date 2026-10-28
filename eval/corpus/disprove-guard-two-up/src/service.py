"""Account operations."""

import repo


def delete_account(conn, account_id):
    repo.purge(conn, account_id)


def record(event, sink=[]):
    sink.append(event)
    return sink
