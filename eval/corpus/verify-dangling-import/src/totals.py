def order_total(items):
    return sum(i["price"] * i["qty"] for i in items)


def legacy_total(items):
    return sum(i["price"] for i in items)
