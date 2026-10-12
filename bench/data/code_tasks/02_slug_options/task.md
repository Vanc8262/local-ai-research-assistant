Add two optional keyword arguments to `slugify`:

- `sep` (default `"-"`): the string used to join the words.
- `max_words` (default `None`): when set, keep only the first `max_words` words.

Existing calls with one argument must keep working.
