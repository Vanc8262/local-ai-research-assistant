Make `load_config` robust:

1. Skip blank lines and lines that start with `#`.
2. Start from a copy of `DEFAULTS` and override it with the values in the text. Never modify `DEFAULTS`.
3. Split each line on the first `=` only, so values may contain `=`.
4. Convert `port` to an `int` and `debug` to a `bool` (`true` or `false`, any letter case).
5. Raise `ValueError` for a line without `=`, for a key that is not in `DEFAULTS`, and for a `port` or `debug` value that cannot be converted. The message must include the 1-based line number as `line N`.
