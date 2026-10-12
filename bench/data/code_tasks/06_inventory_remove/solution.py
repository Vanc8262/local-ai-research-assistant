class Inventory:
    """Counts of items in stock."""

    def __init__(self):
        self._counts = {}

    def add(self, item, quantity=1):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._counts[item] = self._counts.get(item, 0) + quantity

    def remove(self, item, quantity):
        if item not in self._counts:
            raise KeyError(item)
        if quantity <= 0 or quantity > self._counts[item]:
            raise ValueError("quantity must be positive and no more than the stock")
        self._counts[item] -= quantity
        if self._counts[item] == 0:
            del self._counts[item]

    def count(self, item):
        return self._counts.get(item, 0)

    def items(self):
        return sorted(self._counts)

    def total(self):
        return sum(self._counts.values())
