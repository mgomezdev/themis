"""A printer is identified by (IP, model) and create requires the IP (BIZ-369): tests that post printers take a unique one."""
import itertools

_seq = itertools.count(1)


def ip_config(**extra) -> dict:
    """A `connection_config` holding only a fresh documentation-range IP (plus `extra`)."""
    n = next(_seq)
    return {"ip_address": f"198.51.{(n >> 8) & 255}.{n & 255}", **extra}
