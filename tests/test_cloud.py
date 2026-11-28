"""Cloud login tests. Every HTTP call is a fake; no account is built into the library."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from hashlib import sha256
from typing import TYPE_CHECKING, Self

import pytest

from midea_dishwasher_api.cloud import (
    CloudError,
    MSmartHomeCloud,
    NetHomePlusCloud,
    udp_id,
)
from midea_dishwasher_api.cloud._http import post_bytes
from midea_dishwasher_api.cloud._json import _convert

if TYPE_CHECKING:
    from collections.abc import Mapping

    from midea_dishwasher_api.cloud._http import Post

_ACCOUNT = "user@example.com"
_LOGIN = "test-password"
_LOGIN_ID = "login-1"
_DEVICE_ID = 151732606394621
_LITTLE = "c3fd9f4a01a4fd001b89259d53c37a90"
_BIG = "e90837fac74180d291fb726500376230"
_TOKEN = bytes(range(64))
_KEY = bytes(range(32))
_MSMART_HASH = "94f781ea8105d4fa03c9a74fbf29c2f3b906c36d25cb7bd639fb5b7b9dc34db5"
_IAM = "a26c3e5aa9bb21902735b2e731aa4e1b96c76d65a188cb2848b13cef6aeb9d33"
_IAM_CHINA = "236442808fcd83d3a2b844b4daec845d"
_NETHOME_HASH = "d012a96fb56c6a25f5f1b78cc1fb4ea448073e0c079c808a0f95367f1391d5e8"
_APP_KEY = "3742e9e5842d4ad59c2db887e12449f9"
_HMAC_KEY = b"PROD_VnoClJI9aikS8dyy"


class Script:
    """Serves canned response bodies and records every POST."""

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], bytes, float]] = []

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> bytes:
        self.calls.append((url, dict(headers), body, timeout))
        if not self._responses:
            msg = "unexpected cloud request"
            raise AssertionError(msg)
        return self._responses.pop(0)


def _msmart(data: object, *, code: int = 0, msg: str = "ok") -> bytes:
    return json.dumps({"code": code, "msg": msg, "data": data}).encode()


def _nethome(result: object, *, code: str = "0", msg: str = "ok") -> bytes:
    return json.dumps({"errorCode": code, "msg": msg, "result": result}).encode()


def _token_list(udp: str) -> dict[str, object]:
    return {"tokenlist": [{"udpId": udp, "token": _TOKEN.hex(), "key": _KEY.hex()}]}


def _client(script: Script, *, china: bool = False) -> MSmartHomeCloud:
    return MSmartHomeCloud(_ACCOUNT, _LOGIN, use_china_server=china, timeout=4.0, post=script)


def test_udp_id_folds_sha256_the_way_msmart_does() -> None:
    assert udp_id(_DEVICE_ID) == _LITTLE
    assert udp_id(_DEVICE_ID, byteorder="big") == _BIG
    with pytest.raises(ValueError, match="does not fit"):
        udp_id(2**48)


def test_rejects_empty_and_non_ascii_credentials() -> None:
    with pytest.raises(ValueError, match="account and password are required"):
        MSmartHomeCloud("", _LOGIN)
    with pytest.raises(ValueError, match="account and password are required"):
        NetHomePlusCloud(_ACCOUNT, "")
    with pytest.raises(ValueError, match="must be ASCII"):
        MSmartHomeCloud(_ACCOUNT, "pässword")
    with pytest.raises(ValueError, match="must be ASCII"):
        NetHomePlusCloud(_ACCOUNT, "pässword")


def test_msmart_login_and_get_token_tries_both_endians() -> None:
    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            _msmart({"tokenlist": [{"udpId": "someone-else", "token": "aa", "key": "bb"}]}),
            _msmart(_token_list(_BIG)),
        ]
    )
    credentials = _client(script).get_token(_DEVICE_ID)

    assert credentials.udp_id == _BIG
    assert credentials.token == _TOKEN
    assert credentials.key == _KEY
    assert [url.rsplit("=", 1)[-1] for url, *_rest in script.calls] == [
        "/v1/user/login/id/get",
        "/mj/user/login",
        "/v1/iot/secure/getToken",
        "/v1/iot/secure/getToken",
    ]
    login_url, login_headers, login_body, timeout = script.calls[1]
    assert login_url.startswith("https://mp-prod.appsmb.com/")
    assert timeout == 4.0
    assert _LOGIN.encode() not in login_body
    login = json.loads(login_body)
    assert login["iotData"]["password"] == _MSMART_HASH
    assert login["iotData"]["iampwd"] == _IAM
    assert "appId" not in login
    assert login_headers["sign"] == _hmac_of(login_body, login_headers["random"])
    assert script.calls[2][1]["accessToken"] == "access-token"
    get_body = json.loads(script.calls[3][2])
    assert get_body["udpid"] == _BIG
    assert get_body["applianceCodes"] == str(_DEVICE_ID)


def _hmac_of(body: bytes, nonce: str) -> str:
    import hmac

    message = b"meicloud" + body + nonce.encode()
    return hmac.new(_HMAC_KEY, message, sha256).hexdigest()


def test_msmart_login_is_reused_and_little_endian_can_hit() -> None:
    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            _msmart(_token_list(_LITTLE)),
            _msmart(_token_list(_LITTLE)),
        ]
    )
    cloud = _client(script)
    assert cloud.get_token(_DEVICE_ID).udp_id == _LITTLE
    assert cloud.get_token(_DEVICE_ID).udp_id == _LITTLE
    assert len(script.calls) == 4


def test_msmart_china_server_uses_the_china_host_and_iampwd() -> None:
    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
        ]
    )
    cloud = _client(script, china=True)
    cloud.login()
    cloud.login()
    assert script.calls[0][0].startswith("https://mp-prod.smartmidea.net/")
    login = json.loads(script.calls[1][2])
    assert login["iotData"]["iampwd"] == _IAM_CHINA
    assert len(script.calls) == 2


def test_msmart_api_error_stops_before_the_other_endian() -> None:
    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            _msmart({}, code=3004, msg="value is illegal"),
        ]
    )
    with pytest.raises(CloudError, match=r"value is illegal \(3004\)"):
        _client(script).get_token(_DEVICE_ID)
    assert len(script.calls) == 3


def test_msmart_rejects_a_bad_token_and_a_bad_body() -> None:
    short = {"tokenlist": [{"udpId": _LITTLE, "token": "abcd", "key": _KEY.hex()}]}
    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            _msmart(short),
        ]
    )
    with pytest.raises(CloudError, match="token must be 64 bytes"):
        _client(script).get_token(_DEVICE_ID)

    script = Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            b"not-json",
        ]
    )
    with pytest.raises(CloudError, match="invalid JSON"):
        _client(script).get_token(_DEVICE_ID)

    script = Script([_msmart({"loginId": _LOGIN_ID}), b'{"code": 0}'])
    with pytest.raises(CloudError, match="data is missing"):
        _client(script).login()


def test_nethome_login_signs_the_form_and_returns_the_token() -> None:
    script = Script(
        [
            _nethome({"loginId": _LOGIN_ID}),
            _nethome({"sessionId": "session-1"}),
            _nethome({"tokenlist": []}),
            _nethome(_token_list(_BIG)),
        ]
    )
    cloud = NetHomePlusCloud(_ACCOUNT, _LOGIN, timeout=2.5, post=script)
    credentials = cloud.get_token(_DEVICE_ID)

    assert credentials.udp_id == _BIG
    assert credentials.token == _TOKEN
    login_body = script.calls[1][2].decode()
    assert _LOGIN not in login_body
    assert _NETHOME_HASH in login_body
    assert _signed(script.calls[0])
    assert _signed(script.calls[1])
    assert "sessionId=session-1" in script.calls[3][2].decode()
    assert script.calls[0][3] == 2.5
    cloud.login()
    assert len(script.calls) == 4


def _signed(call: tuple[str, dict[str, str], bytes, float]) -> bool:
    from urllib.parse import parse_qs, unquote_plus, urlencode

    url, _headers, body, _timeout = call
    parsed = {
        key: values[0] for key, values in parse_qs(body.decode(), keep_blank_values=True).items()
    }
    sign = parsed.pop("sign")
    path = url.removeprefix("https://mapp.appsmb.com")
    query = unquote_plus(urlencode(sorted(parsed.items())))
    expected = sha256((path + query + _APP_KEY).encode()).hexdigest()
    return sign == expected


def test_nethome_error_code_and_missing_session() -> None:
    script = Script([_nethome({}, code="12", msg="denied")])
    with pytest.raises(CloudError, match=r"denied \(12\)"):
        NetHomePlusCloud(_ACCOUNT, _LOGIN, post=script).login()

    script = Script([_nethome({"loginId": _LOGIN_ID}), _nethome({})])
    with pytest.raises(CloudError, match="sessionId is missing"):
        NetHomePlusCloud(_ACCOUNT, _LOGIN, post=script).login()


def test_token_list_problems_are_reported() -> None:
    from midea_dishwasher_api.cloud._token import match_token

    with pytest.raises(CloudError, match="tokenlist is missing"):
        match_token({"tokenlist": "nope"}, _LITTLE)
    with pytest.raises(CloudError, match="token is not hex"):
        match_token(
            {"tokenlist": [{"udpId": _LITTLE, "token": "zz", "key": _KEY.hex()}]},
            _LITTLE,
        )
    with pytest.raises(CloudError, match="key is missing"):
        match_token({"tokenlist": [{"udpId": _LITTLE, "token": _TOKEN.hex()}]}, _LITTLE)
    assert match_token({"tokenlist": ["not-an-object", {"udpId": "other"}]}, _LITTLE) is None
    with pytest.raises(CloudError, match="no token for this device"):
        MSmartHomeCloud(_ACCOUNT, _LOGIN, post=_empty_token_script()).get_token(_DEVICE_ID)


def _empty_token_script() -> Post:
    return Script(
        [
            _msmart({"loginId": _LOGIN_ID}),
            _msmart({"mdata": {"accessToken": "access-token"}}),
            _msmart({"tokenlist": []}),
            _msmart({"tokenlist": []}),
        ]
    )


class _Opened:
    """Stand-in for the object ``urlopen`` returns."""

    def __init__(self, payload: bytes | str) -> None:
        self._payload: bytes | str = payload

    def read(self) -> bytes | str:
        """Return the scripted body."""
        return self._payload

    def __enter__(self) -> Self:
        """Return this response."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Ignore the context-manager arguments."""
        return


def test_post_bytes_returns_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def succeed(_request: object, timeout: float = 0) -> _Opened:
        assert timeout == 3
        return _Opened(b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", succeed)
    assert (
        post_bytes("https://example.test/login", {"Content-Type": "application/json"}, b"{}", 3)
        == b'{"ok":true}'
    )


def test_post_bytes_rejects_a_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Opened("not-bytes"))
    with pytest.raises(CloudError, match="response was not bytes"):
        post_bytes("https://example.test", {}, b"", 1)


def test_post_bytes_wraps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def http_error(_request: object, timeout: float = 0) -> _Opened:
        assert timeout == 1
        url = "https://example.test"
        reason = "down"
        raise urllib.error.HTTPError(url, 503, reason, hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    with pytest.raises(CloudError, match="HTTP 503"):
        post_bytes("https://example.test", {}, b"", 1)

    def url_error(_request: object, timeout: float = 0) -> _Opened:
        assert timeout == 1
        reason = "timed out"
        raise urllib.error.URLError(reason)

    monkeypatch.setattr(urllib.request, "urlopen", url_error)
    with pytest.raises(CloudError, match="timed out"):
        post_bytes("https://example.test", {}, b"", 1)


def test_json_converter_rejects_a_non_json_value() -> None:
    with pytest.raises(CloudError, match="unsupported JSON value"):
        _convert(object())
    assert _convert({1: "skipped", "kept": None}) == {"kept": None}


def test_code_must_be_a_number() -> None:
    script = Script([b'{"code": true, "data": {}}'])
    with pytest.raises(CloudError, match="code is missing"):
        _client(script).login()
    script = Script([b'{"code": "nope", "data": {}}'])
    with pytest.raises(CloudError, match="code is missing"):
        _client(script).login()
    script = Script([b"[]"])
    with pytest.raises(CloudError, match="response is missing"):
        _client(script).login()
