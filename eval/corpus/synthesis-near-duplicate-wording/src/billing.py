def invoice_total(lines, tax_rate):
    subtotal = sum(l["price"] * l["qty"] for l in lines)
    return round(subtotal * (1 + tax_rate), 2)


def apply_discount(total, pct):
    return total - total * pct / 100


def split_evenly(total, parts):
    return [round(total / parts, 2)] * parts
