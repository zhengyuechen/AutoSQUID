"""Small generic helpers with no domain or hardware dependencies."""


def clamp(x, lo, hi):
    "Clamp x into [lo, hi] (as float)."
    return max(lo, min(hi, float(x)))
