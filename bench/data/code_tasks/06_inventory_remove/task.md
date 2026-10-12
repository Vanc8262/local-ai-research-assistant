Add two methods to `Inventory`:

- `remove(item, quantity)`: take `quantity` units of `item` out of stock. Raise `KeyError` if the item is not in stock, and `ValueError` if `quantity` is not positive or is more than the amount in stock. When an item's count reaches zero, delete it so it no longer appears in `items()`.
- `total()`: return the total number of units across all items.

Do not change the behaviour of the existing methods.
