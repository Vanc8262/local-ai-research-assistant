class Inventory:
    """Counts of items in stock."""

    def __init__(self):
        self._counts = {}

    def add(self, item, quantity=1):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._counts[item] = self._counts.get(item, 0) + quantity

    def count(self, item):
        return self._counts.get(item, 0)

    def items(self):
        return sorted(self._counts)
