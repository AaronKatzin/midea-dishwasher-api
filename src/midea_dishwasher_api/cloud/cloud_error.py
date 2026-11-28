"""Error raised when the Midea cloud rejects or drops a request."""

from __future__ import annotations


class CloudError(Exception):
    """Login or getToken failed, or the response could not be read."""
