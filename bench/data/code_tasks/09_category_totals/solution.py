def summarize(rows):
    """rows: list of dicts with 'category' and 'amount' strings. Return {category: total}."""
    totals = {}
    for row in rows:
        category = row["category"].strip().lower()
        if not category:
            continue
        try:
            amount = float(row["amount"])
        except ValueError:
            continue
        totals[category] = totals.get(category, 0.0) + amount
    return {category: round(total, 2) for category, total in totals.items()}
