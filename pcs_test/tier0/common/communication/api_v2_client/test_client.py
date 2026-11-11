import logging
from unittest import TestCase, mock
from urllib.error import URLError
from urllib.request import Request

from pcs.common.communication.api_v2_client import client
from pcs.common.communication.api_v2_client.client import (
    _ApiV2Client,
    _request_data,
)
from pcs.common.communication.api_v2_client.errors import (
    ApiV2CommunicationError,
    ApiV2ConnectionError,
    ApiV2TimeoutError,
)
from pcs.common.communication.api_v2_client.transport import Transport

LABEL = "/run/pcsd.sock"
TIMEOUT = 30.0
BASE_URL = "http://pcsd.local"


def _log_started(url, data=""):
    if not data:
        return mock.call.debug("Sending HTTP request to: %s", url)
    return mock.call.debug(
        "Sending HTTP request to: %s\n"
        "--Debug Input Start--\n%s\n--Debug Input End--",
        url,
        data,
    )


class _FakeTransport(Transport):
    """
    Concrete Transport that builds real Request objects (so full_url and
    request data are realistic) but routes open() through a mock.
    """

    def __init__(self, label=LABEL):
        self._label = label
        self.open_mock = mock.MagicMock(name="open")

    @property
    def label(self):
        return self._label

    def _build_url(self, path):
        return f"{BASE_URL}{path}"

    def open(self, request, timeout):
        return self.open_mock(request, timeout)


class RequestData(TestCase):
    def test_none(self):
        self.assertEqual("", _request_data(Request("http://x")))

    def test_bytes(self):
        self.assertEqual(
            "body", _request_data(Request("http://x", data=b"body"))
        )

    def test_str_fallback(self):
        request = Request("http://x")
        request.data = 42
        self.assertEqual("42", _request_data(request))


class SendRequestBase(TestCase):
    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()
        self.client = _ApiV2Client(
            self.transport, self.logger, api_v2_timeout=TIMEOUT
        )
        # A GET request keeps request data empty and gives a stable full_url.
        self.request = self.transport.build_request(
            "/api/v2/task/result", query_params={"task_ident": "tid"}
        )
        self.full_url = f"{BASE_URL}/api/v2/task/result?task_ident=tid"


class SendRequestUrlError(SendRequestBase):
    def setUp(self):
        super().setUp()
        # Do not depend on proxy variables set in the environment running the
        # tests. Tests checking the proxy warning override this patch.
        patcher = mock.patch.object(client, "is_proxy_set", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_timeout_reason(self):
        self.transport.open_mock.side_effect = URLError(
            TimeoutError("timed out")
        )
        with self.assertRaises(ApiV2TimeoutError) as cm:
            self.client._send_request(self.request)
        self.assertEqual(cm.exception.endpoint, LABEL)
        self.assertEqual(cm.exception.timeout, TIMEOUT)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Calling %s timed out: %s", self.full_url, "timed out"
                ),
            ],
        )

    def test_oserror_reason_is_connection_error(self):
        self.transport.open_mock.side_effect = URLError(
            ConnectionRefusedError(111, "Connection refused")
        )
        with self.assertRaises(ApiV2ConnectionError) as cm:
            self.client._send_request(self.request)
        self.assertEqual(cm.exception.endpoint, LABEL)
        self.assertEqual(cm.exception.reason, "[Errno 111] Connection refused")
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Unable to connect to %s when calling %s: %s",
                    LABEL,
                    self.full_url,
                    "[Errno 111] Connection refused",
                ),
            ],
        )

    def test_other_reason_is_communication_error(self):
        self.transport.open_mock.side_effect = URLError("boom")
        with self.assertRaises(ApiV2CommunicationError) as cm:
            self.client._send_request(self.request)
        self.assertEqual(cm.exception.reason, "boom")
        self.assertIsNone(cm.exception.http_code)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug("Error calling %s: %s", self.full_url, "boom"),
            ],
        )

    @mock.patch.object(client, "is_proxy_set", return_value=True)
    def test_proxy_set_adds_warning(self, _mock_proxy):
        self.transport.open_mock.side_effect = URLError("boom")
        with self.assertRaises(ApiV2CommunicationError):
            self.client._send_request(self.request)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.warning(
                    "Proxy is set in environment variables, try disabling it"
                ),
                mock.call.debug("Error calling %s: %s", self.full_url, "boom"),
            ],
        )


class SendRequestOtherErrors(SendRequestBase):
    def test_bare_timeout_error(self):
        self.transport.open_mock.side_effect = TimeoutError("slow")
        with self.assertRaises(ApiV2TimeoutError) as cm:
            self.client._send_request(self.request)
        self.assertEqual(cm.exception.timeout, TIMEOUT)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Calling %s timed out: %s", self.full_url, "slow"
                ),
            ],
        )

    def test_unexpected_exception(self):
        self.transport.open_mock.side_effect = ValueError("x")
        with self.assertRaises(ApiV2CommunicationError) as cm:
            self.client._send_request(self.request)
        self.assertEqual(cm.exception.reason, "Unexpected error: ValueError")
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Unexpected error when calling %s: %s",
                    self.full_url,
                    "ValueError",
                ),
            ],
        )
