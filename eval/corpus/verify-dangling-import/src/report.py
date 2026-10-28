from totals import legacy_total, order_total


def summary(items):
    return {"total": order_total(items), "list_price": legacy_total(items)}
