"""
MSmartHome login and getToken.

The account and password belong to the caller. This module does not embed one.
The app id and signing keys below are the published mobile-app constants from
msmart-ng (``msmart/cloud.py``, ``SmartHomeCloud``); they identify the app to
the API and are not a device token or key.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from datetime import UTC, datetime
from hashlib import md5, sha256
from typing import TYPE_CHECKING

from ._http import Post, post_bytes
from ._json import JsonObject, expect_code, expect_object, expect_str, loads
from ._token import first_credentials, match_token
from .cloud_error import CloudError

if TYPE_CHECKING:
    from .device_credentials import DeviceCredentials

log: logging.Logger = logging.getLogger("midea_dishwasher_api.cloud")

_APP_ID = "1010"
_HMAC_KEY = "PROD_VnoClJI9aikS8dyy"
_IOT_KEY = "meicloud"
_IOT_KEY_CHINA = "prod_secret123@muc"
_LOGIN_KEY = "ac21b9f9cbfe4ca5a88562ef25e2b768"
_LOGIN_KEY_CHINA = "ad0ee21d48a64bf49f4fb583ab76e799"
_BASE_URL = "https://mp-prod.appsmb.com"
_BASE_URL_CHINA = "https://mp-prod.smartmidea.net"
_FORMAT = 2
_CLIENT_TYPE = 1


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


def _password(login_id: str, password: str, login_key: str) -> str:
    """Hash the password the way ``/mj/user/login`` expects the ``password`` field."""
    hashed = sha256(password.encode("ascii")).hexdigest()
    return sha256((login_id + hashed + login_key).encode("ascii")).hexdigest()


def _iampwd(login_id: str, password: str, login_key: str, *, china: bool) -> str:
    """
    Hash the password the way ``/mj/user/login`` expects the ``iampwd`` field.

    The MSmartHome app hashes this field with MD5. That is the API, not a choice.
    """
    first = md5(password.encode("ascii")).hexdigest()
    second = md5(first.encode("ascii")).hexdigest()
    if china:
        return second
    return sha256((login_id + second + login_key).encode("ascii")).hexdigest()


class MSmartHomeCloud:
    """Session on the MSmartHome cloud (``mp-prod.appsmb.com``)."""

    def __init__(
        self,
        account: str,
        password: str,
        *,
        use_china_server: bool = False,
        timeout: float = 10.0,
        post: Post | None = None,
    ) -> None:
        """Store the caller's account. Nothing is sent until ``login`` or ``get_token``."""
        _require_credentials(account, password)
        self._account: str = account
        self._password: str = password
        self._timeout: float = timeout
        self._post: Post = post or post_bytes
        self._base_url: str = _BASE_URL_CHINA if use_china_server else _BASE_URL
        self._iot_key: str = _IOT_KEY_CHINA if use_china_server else _IOT_KEY
        self._login_key: str = _LOGIN_KEY_CHINA if use_china_server else _LOGIN_KEY
        self._china: bool = use_china_server
        self._client_id: str = secrets.token_hex(8)
        self._login_id: str | None = None
        self._access_token: str = ""
        self._appliance_id: int = 0

    def login(self) -> None:
        """Open a session, reusing one that is already open."""
        if self._access_token != "":
            return
        if self._login_id is None:
            self._login_id = self._fetch_login_id()
        self._access_token = self._fetch_access_token(self._login_id)
        log.info("logged in to MSmartHome")

    def get_token(self, device_id: int) -> DeviceCredentials:
        """Log in if needed and return the LAN token and key for this device."""
        self.login()
        self._appliance_id = device_id
        return first_credentials(device_id, self._fetch_token)

    def _fetch_login_id(self) -> str:
        """Ask the cloud for the login id that salts the password hash."""
        data = self._api("/v1/user/login/id/get", self._fields({"loginAccount": self._account}))
        return expect_str(data.get("loginId"), "loginId")

    def _fetch_access_token(self, login_id: str) -> str:
        """Exchange the hashed password for an access token."""
        body: JsonObject = {
            "data": {"platform": _FORMAT, "deviceId": self._client_id},
            "iotData": {
                "appId": _APP_ID,
                "src": _APP_ID,
                "clientType": _CLIENT_TYPE,
                "loginAccount": self._account,
                "iampwd": _iampwd(login_id, self._password, self._login_key, china=self._china),
                "password": _password(login_id, self._password, self._login_key),
                "pushToken": secrets.token_urlsafe(120),
                "stamp": _stamp(),
                "reqId": secrets.token_hex(16),
            },
        }
        data = self._api("/mj/user/login", body)
        mdata = expect_object(data.get("mdata"), "mdata")
        return expect_str(mdata.get("accessToken"), "accessToken")

    def _fetch_token(self, udp: str) -> tuple[bytes, bytes] | None:
        """Call getToken for one UDP id of the device passed to ``get_token``."""
        data = self._api(
            "/v1/iot/secure/getToken",
            self._fields({"udpid": udp, "applianceCodes": str(self._appliance_id)}),
        )
        return match_token(data, udp)

    def _fields(self, extra: JsonObject) -> JsonObject:
        """Build the flat request body the proxy methods share."""
        body: JsonObject = {
            "appId": _APP_ID,
            "src": _APP_ID,
            "format": _FORMAT,
            "clientType": _CLIENT_TYPE,
            "language": "en_US",
            "deviceId": self._client_id,
            "stamp": _stamp(),
            "reqId": secrets.token_hex(16),
        }
        body.update(extra)
        return body

    def _api(self, endpoint: str, body: JsonObject) -> JsonObject:
        """POST a signed JSON body and return its ``data`` object."""
        raw = json.dumps(body).encode("ascii")
        nonce = secrets.token_hex(16)
        headers = {
            "Content-Type": "application/json",
            "secretVersion": "1",
            "sign": _sign(self._iot_key, raw, nonce),
            "random": nonce,
            "accessToken": self._access_token,
        }
        url = f"{self._base_url}/mas/v5/app/proxy?alias={endpoint}"
        log.debug("MSmartHome %s", endpoint)
        payload = self._post(url, headers, raw, self._timeout)
        return _data(payload)


def _sign(iot_key: str, raw: bytes, nonce: str) -> str:
    """HMAC-SHA256 of ``iot_key || body || nonce``, as the proxy requires."""
    message = iot_key.encode("ascii") + raw + nonce.encode("ascii")
    return hmac.new(_HMAC_KEY.encode("ascii"), message, sha256).hexdigest()


def _data(payload: bytes) -> JsonObject:
    """Return the ``data`` object of a successful response."""
    body = expect_object(loads(payload), "response")
    code = expect_code(body.get("code"))
    if code != 0:
        message = body.get("msg")
        text = message if isinstance(message, str) else "error"
        msg = f"Failed to call cloud: {text} ({code})"
        raise CloudError(msg)
    return expect_object(body.get("data"), "data")
