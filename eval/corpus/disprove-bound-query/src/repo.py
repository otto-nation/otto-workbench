"""Order queries."""


def orders_for(conn, customer_id):
    cur = conn.execute("SELECT id, total FROM orders WHERE customer_id = ?", (customer_id,))
    return cur.fetchall()


def search_notes(conn, term):
    query = f"SELECT id, body FROM notes WHERE body LIKE '%{term}%'"
    return conn.execute(query).fetchall()
