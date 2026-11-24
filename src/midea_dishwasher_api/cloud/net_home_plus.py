"""
NetHome Plus login and getToken.

The account and password belong to the caller. This module does not embed one.
The app id and app key below are the published mobile-app constants from
msmart-ng (``msmart/cloud.py``, ``NetHomePlusCloud``); they identify the app
to the API and are not a device token or key.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING
from urllib.parse import unquote_plus, urlencode

from ._http import Post, post_bytes
from ._json import JsonObject, expect_code, expect_object, expect_str, loads
from ._token import first_credentials, match_token
from .cloud_error import CloudError

if TYPE_CHECKING:
    from .device_credentials import DeviceCredentials

log: logging.Logger = logging.getLogger("midea_dishwasher_api.cloud")

_APP_ID = "1017"
_APP_KEY = "3742e9e5842d4ad59c2db887e12449f9"
_BASE_URL = "https://mapp.appsmb.com"


def _require_credentials(account: str, password: str) -> None:
    """Reject an empty or non-ASCII login before any request is sent."""
    if account == "" or password == "":
        msg = "Failed to build cloud client: account and password are required"
        raise ValueError(msg)
    try:
        account.encode("ascii")
        password.encode("ascii")
    except UnicodeEncodeError as error:
        msg = "Failed to build cloud client: account and password must be ASCII"
        raise ValueError(msg) from error


def _stamp() -> str:
    """Return the UTC timestamp the API puts in every body."""
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _password(login_id: str, password: str) -> str:
    """Hash the password the way ``/v1/user/login`` expects it."""
    hashed = sha256(password.encode("ascii")).hexdigest()
    return sha256((login_id + hashed + _APP_KEY).encode("ascii")).hexdigest()


def _sign(path: str, fields: dict[str, str]) -> str:
    """SHA-256 of ``path + sorted query + app key``."""
    query = unquote_plus(urlencode(sorted(fields.items())))
    return sha256((path + query + _APP_KEY).encode("ascii")).hexdigest()


class NetHomePlusCloud:
    """Session on the NetHome Plus cloud (``mapp.appsmb.com``)."""

    def __init__(
        self,
        account: str,
        password: str,
        *,
        timeout: float = 10.0,
        post: Post | None = None,
    ) -> None:
        """Store the caller's account. Nothing is sent until ``login`` or ``get_token``."""
        _require_credentials(account, password)
        self._account: str = account
        self._password: str = password
        self._timeout: float = timeout
        self._post: Post = post or post_bytes
        self._client_id: str = secrets.token_hex(8)
        self._login_id: str | None = None
        self._session_id: str = ""
        self._appliance_id: int = 0

    def login(self) -> None:
        """Open a session, reusing one that is already open."""
        if self._session_id != "":
            return
        if self._login_id is None:
            self._login_id = self._fetch_login_id()
        self._session_id = self._fetch_session(self._login_id)
        log.info("logged in to NetHome Plus")

    def get_token(self, device_id: int) -> DeviceCredentials:
        """Log in if needed and return the LAN token and key for this device."""
        self.login()
        self._appliance_id = device_id
        return first_credentials(device_id, self._fetch_token)

    def _fetch_login_id(self) -> str:
        """Ask the cloud for the login id that salts the password hash."""
        data = self._api("/v1/user/login/id/get", {"loginAccount": self._account})
        return expect_str(data.get("loginId"), "loginId")

    def _fetch_session(self, login_id: str) -> str:
        """Exchange the hashed password for a session id."""
        data = self._api(
            "/v1/user/login",
            {
                "loginAccount": self._account,
                "password": _password(login_id, self._password),
            },
        )
        return expect_str(data.get("sessionId"), "sessionId")

    def _fetch_token(self, udp: str) -> tuple[bytes, bytes] | None:
        """Call getToken for one UDP id of the device passed to ``get_token``."""
        data = self._api(
            "/v1/iot/secure/getToken",
            {"udpid": udp, "applianceCodes": str(self._appliance_id)},
        )
        return match_token(data, udp)

    def _fields(self, extra: dict[str, str]) -> dict[str, str]:
        """Build the form body, including the session id once login has one."""
        fields = {
            "appId": _APP_ID,
            "src": _APP_ID,
            "format": "2",
            "clientType": "1",
            "language": "en_US",
            "deviceId": self._client_id,
            "stamp": _stamp(),
            "sessionId": self._session_id,
        }
        fields.update(extra)
        return fields

    def _api(self, endpoint: str, extra: dict[str, str]) -> JsonObject:
        """POST a signed form body and return its ``result`` object."""
        fields = self._fields(extra)
        signed = dict(fields)
        signed["sign"] = _sign(endpoint, fields)
        body = urlencode(sorted(signed.items())).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        url = f"{_BASE_URL}{endpoint}"
        log.debug("NetHome Plus %s", endpoint)
        payload = self._post(url, headers, body, self._timeout)
        return _result(payload)


def _result(payload: bytes) -> JsonObject:
    """Return the ``result`` object of a successful response."""
    body = expect_object(loads(payload), "response")
    code = expect_code(body.get("errorCode"))
    if code != 0:
        message = body.get("msg")
        text = message if isinstance(message, str) else "error"
        msg = f"Failed to call cloud: {text} ({code})"
        raise CloudError(msg)
    return expect_object(body.get("result"), "result")
