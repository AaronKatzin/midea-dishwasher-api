"""Error raised when LAN discovery cannot run."""

from __future__ import annotations


class DiscoveryError(Exception):
    """The discovery socket could not be opened or used."""
