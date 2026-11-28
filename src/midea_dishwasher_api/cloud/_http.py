"""HTTP POST used by the cloud clients. The body is whatever the caller signed."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from .cloud_error import CloudError

Post = Callable[[str, Mapping[str, str], bytes, float], bytes]


def post_bytes(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
    """POST ``body`` and return the response bytes."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        msg = f"Failed to call cloud: HTTP {error.code}"
        raise CloudError(msg) from error
    except urllib.error.URLError as error:
        msg = f"Failed to call cloud: {error.reason}"
        raise CloudError(msg) from error
    if isinstance(payload, bytes):
        return payload
    msg = "Failed to call cloud: response was not bytes"
    raise CloudError(msg)
