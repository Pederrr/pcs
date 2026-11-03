from unittest import TestCase, mock
from urllib.request import Request

from pcs.common.communication.api_v2_client import transport
from pcs.common.communication.api_v2_client.transport import (
    TcpTransport,
    Transport,
    UnixSocketTransport,
    _format_addr_for_url,
)

SOCKET_PATH = "/run/pcsd.sock"
ADDR = "node1.example.com"
PORT = 2224
TOKEN = "secret-token"
URL_PATH = "/foo/bar"


class FormatAddrForUrl(TestCase):
    def test_hostname(self):
        self.assertEqual(
            "node1.example.com", _format_addr_for_url("node1.example.com")
        )

    def test_ipv4(self):
        self.assertEqual("192.0.2.1", _format_addr_for_url("192.0.2.1"))

    def test_ipv6_is_bracketed(self):
        self.assertEqual("[::1]", _format_addr_for_url("::1"))

    def test_ipv6_full_is_bracketed(self):
        self.assertEqual("[2001:db8::1]", _format_addr_for_url("2001:db8::1"))


class _StubTransport(Transport):
    """
    Minimal concrete Transport exercising the shared build_request logic
    without any real I/O.
    """

    def __init__(self, label="stub"):
        self._label = label

    @property
    def label(self):
        return self._label

    def _build_url(self, path):
        return f"http://stub{path}"

    def open(self, request, timeout):
        del request, timeout
        raise AssertionError("open should not be called in these tests")


class BuildRequest(TestCase):
    def setUp(self):
        self.transport = _StubTransport()

    def test_path_must_start_with_slash(self):
        with self.assertRaises(AssertionError) as cm:
            self.transport.build_request("api/v2/task/create")
        self.assertIn("api/v2/task/create", str(cm.exception))

    def test_get_request_without_body(self):
        request = self.transport.build_request(URL_PATH)

        self.assertEqual(request.full_url, f"http://stub{URL_PATH}")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertFalse(request.has_header("Content-type"))

    def test_post_request_with_body(self):
        body = b'{"a": 1}'
        request = self.transport.build_request(URL_PATH, json_body=body)

        self.assertEqual(request.full_url, f"http://stub{URL_PATH}")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, body)
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_query_params_are_appended(self):
        request = self.transport.build_request(
            URL_PATH, query_params={"task_ident": "tid"}
        )

        self.assertEqual(
            request.full_url,
            f"http://stub{URL_PATH}?task_ident=tid",
        )

    def test_query_params_are_url_encoded(self):
        request = self.transport.build_request(
            URL_PATH, query_params={"task_ident": "a b/c", "x": "1&2"}
        )

        self.assertEqual(
            request.full_url,
            f"http://stub{URL_PATH}?task_ident=a+b%2Fc&x=1%262",
        )

    def test_empty_query_params_are_ignored(self):
        request = self.transport.build_request(URL_PATH, query_params={})

        self.assertEqual(request.full_url, f"http://stub{URL_PATH}")


class TcpTransportTest(TestCase):
    def setUp(self):
        self.transport = TcpTransport(ADDR, PORT, TOKEN)

    def test_label_is_host_and_port(self):
        self.assertEqual(self.transport.label, f"{ADDR}:{PORT}")

    def test_label_brackets_ipv6(self):
        self.assertEqual(
            TcpTransport("::1", PORT, TOKEN).label, f"[::1]:{PORT}"
        )

    def test_build_url_is_https(self):
        self.assertEqual(
            self.transport._build_url(URL_PATH),
            f"https://{ADDR}:{PORT}{URL_PATH}",
        )

    def test_build_request_get_has_token_cookie(self):
        request = self.transport.build_request(URL_PATH)

        self.assertEqual(request.full_url, f"https://{ADDR}:{PORT}{URL_PATH}")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Cookie"), f"token={TOKEN}")
        self.assertFalse(request.has_header("Content-type"))

    def test_build_request_post_has_token_cookie_and_content_type(self):
        request = self.transport.build_request(URL_PATH, json_body=b"{}")

        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Cookie"), f"token={TOKEN}")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    @mock.patch.object(transport, "urlopen")
    def test_open_uses_unverified_context_and_timeout(self, mock_urlopen):
        request = Request(f"https://{ADDR}:{PORT}/api/v2/task/result")

        result = self.transport.open(request, 12.5)

        self.assertIs(result, mock_urlopen.return_value)
        mock_urlopen.assert_called_once_with(
            request,
            context=transport._UNVERIFIED_SSL_CONTEXT,
            timeout=12.5,
        )


class UnixSocketTransportTest(TestCase):
    def setUp(self):
        self.transport = UnixSocketTransport(SOCKET_PATH)

    def test_label_is_socket_path(self):
        self.assertEqual(self.transport.label, SOCKET_PATH)

    def test_build_url_uses_unix_scheme(self):
        self.assertEqual(
            self.transport._build_url(URL_PATH), f"unix://localhost{URL_PATH}"
        )

    def test_build_request_get(self):
        request = self.transport.build_request(URL_PATH)

        self.assertEqual(request.full_url, f"unix://localhost{URL_PATH}")
        self.assertEqual(request.get_method(), "GET")
        self.assertFalse(request.has_header("Content-type"))

    def test_build_request_post(self):
        request = self.transport.build_request(URL_PATH, json_body=b"{}")

        self.assertEqual(request.full_url, f"unix://localhost{URL_PATH}")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    @mock.patch.object(transport, "build_opener")
    def test_constructor_builds_opener_with_handler(self, mock_build_opener):
        with mock.patch.object(
            transport, "UnixSocketHttpHandler"
        ) as mock_handler:
            UnixSocketTransport(SOCKET_PATH)

        mock_handler.assert_called_once_with(SOCKET_PATH)
        mock_build_opener.assert_called_once_with(mock_handler.return_value)

    def test_open_delegates_to_opener_with_timeout(self):
        request = Request("unix://localhost/api/v2/task/result")
        self.transport._opener = mock.MagicMock(name="opener")

        result = self.transport.open(request, 7.0)

        self.assertIs(result, self.transport._opener.open.return_value)
        self.transport._opener.open.assert_called_once_with(
            request, timeout=7.0
        )
