Implement `parse_duration`. It converts strings such as `"1h30m"`, `"45s"` or `"2h5m10s"` into a number of seconds.

Rules: the units are `h`, `m` and `s`; each may appear at most once and they must appear in that order; each is preceded by a whole number. Anything else, including an empty string or a bare number, raises `ValueError`.
