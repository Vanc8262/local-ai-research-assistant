`add_tag` has two bugs: separate calls share state through the default argument, and it modifies the list the caller passes in. Fix both. It must return a new list every time.
