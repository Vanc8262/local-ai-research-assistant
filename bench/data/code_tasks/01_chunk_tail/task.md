`chunk` drops the final items when the list length is not a multiple of `size`. Fix it so the last chunk is returned even when it is shorter.
