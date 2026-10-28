"""Row storage for accounts."""


def purge(conn, account_id):
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
