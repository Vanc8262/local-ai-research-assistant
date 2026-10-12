Rewrite `format_report` so every line has three fixed-width columns:

1. the item name, left-aligned in 12 characters (longer names are cut to 12);
2. the quantity, right-aligned in 4 characters;
3. the line total (quantity × unit price), right-aligned in 10 characters with exactly 2 decimals.

After the item lines add a final line with `TOTAL` in the first column, 4 spaces in the second, and the grand total in the third. An empty list produces only the `TOTAL` line with `0.00`. Lines are joined with `\n` and there is no trailing newline.
