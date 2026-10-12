`summarize` should return the total amount per category, but it currently keeps only the last row of each category. Fix it, and also:

- Treat categories case-insensitively and ignore surrounding spaces. Use the lower-case name as the key.
- Skip rows whose amount is empty or not a number. Skip rows whose category is empty.
- Round each total to 2 decimal places.
