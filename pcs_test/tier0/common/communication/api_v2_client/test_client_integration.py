"""
Integration tests for the APIv2 client.

Unlike test_client.py, which mocks ``Transport.open`` and therefore never
exercises the socket/HTTP layer, these tests drive the *real* client stack
against a real HTTP server bound to a real socket. Only the server-side
responses are scripted; the whole client stack, including urllib's error
translation and (for HTTPS) the TLS handshake, runs for real.
"""

import json
import logging
import os
import shutil
import socket
import socketserver
import ssl
import tempfile
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import TestCase, mock

from pcs.common import ssl as pcs_ssl
from pcs.common.async_tasks.dto import (
    CommandDto,
    CommandOptionsDto,
    TaskResultDto,
)
from pcs.common.async_tasks.types import (
    TaskFinishType,
    TaskKillReason,
    TaskState,
)
from pcs.common.communication.api_v2_client import client
from pcs.common.communication.api_v2_client.client import ApiV2Connector
from pcs.common.communication.api_v2_client.errors import (
    ApiV2CommunicationError,
    ApiV2ConnectionError,
    ApiV2ProtocolError,
    ApiV2RequestError,
    ApiV2TimeoutError,
)
from pcs.common.communication.api_v2_client.transport import TcpTransport
from pcs.common.interface.dto import to_dict

from pcs_test.tools.misc import get_tmp_dir

TIMEOUT = 30.0
TOKEN = "secret-token"


def _write_self_signed_cert(tmp_dir, server_name="127.0.0.1"):
    key = pcs_ssl.generate_key()
    cert = pcs_ssl.generate_cert(key, server_name)
    cert_path = os.path.join(tmp_dir, "server.cert")
    key_path = os.path.join(tmp_dir, "server.key")
    with open(cert_path, "wb") as cert_file:
        cert_file.write(pcs_ssl.dump_cert(cert))
    with open(key_path, "wb") as key_file:
        key_file.write(pcs_ssl.dump_key(key))
    return cert_path, key_path


def _bypass_proxy_for_localhost(test_case):
    """
    Make urllib connect to the local test server directly even if a proxy is
    configured in the environment (https_proxy, all_proxy). Otherwise HTTPS
    requests to 127.0.0.1 would be sent to the proxy and the tests would fail.
    """
    patcher = mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1"})
    patcher.start()
    test_case.addCleanup(patcher.stop)


class _RecordedRequest:
    def __init__(self, method, path, headers, body):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body


class _RecordingHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 so responses carry Content-Length and the client knows when the
    # body ends.
    protocol_version = "HTTP/1.1"

    def log_message(self, *args, **kwargs):
        # Silence the default stderr request logging.
        del args, kwargs

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.recorded_requests.append(
            _RecordedRequest(
                method=self.command,
                path=self.path,
                headers=dict(self.headers),
                body=body.decode("utf-8"),
            )
        )
        status, response_body, delay = self.server.next_response()
        if delay:
            # Used to trigger a client-side socket read timeout.
            self.server.wait_event.wait(delay)
        encoded = response_body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except OSError:
            # The client may have already given up (e.g. timeout test); writing
            # to a closed socket is expected and harmless here.
            pass

    do_GET = _handle
    do_POST = _handle


class _ScriptableServerMixin:
    """Shared request-recording and response-scripting state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_requests = []
        self._scripted_responses = deque()
        # Requests that arrived after every scripted response was used up.
        self.unexpected_request_count = 0
        # Lets a delayed handler wake up immediately on shutdown. The server
        # is single-threaded, so shutdown() would otherwise block until the
        # delay ran out.
        self.wait_event = threading.Event()
        # Set once the server has picked a response for a request. Lets tests
        # in which the client gives up early (timeout) wait until the server
        # has actually handled the request before tearing it down.
        self.response_picked_event = threading.Event()

    def add_response(self, status, body, delay=0.0):
        self._scripted_responses.append((status, body, delay))

    @property
    def unused_script_count(self):
        return len(self._scripted_responses)

    def next_response(self):
        self.response_picked_event.set()
        if self._scripted_responses:
            return self._scripted_responses.popleft()
        # No script left for this request. Count it and answer with an
        # explicit error (rather than hanging the client or returning a bland
        # 200) so an unscripted request surfaces legibly at runtime. The
        # authoritative check is assert_all_scripts_consumed in teardown.
        self.unexpected_request_count += 1
        return (500, json.dumps({"detail": "unscripted request"}), 0.0)

    def handle_error(self, request, client_address):
        # Suppress tracebacks from writes to a client that already
        # disconnected, or from aborted TLS handshakes.
        del request, client_address


# The servers are deliberately single-threaded: the client issues requests
# strictly one after another and urllib sends "Connection: close", so one
# connection is served at a time. This also means shutdown() returns only once
# the in-flight request has been fully handled.
class _ScriptableUnixServer(
    _ScriptableServerMixin, socketserver.UnixStreamServer
):
    pass


class _ScriptableHttpsServer(_ScriptableServerMixin, HTTPServer):
    pass


def _task_ident_body(task_ident="tid"):
    return json.dumps({"task_ident": task_ident})


def _result_body(
    *,
    finish_type=TaskFinishType.SUCCESS,
    reports=(),
    result=None,
    kill_reason=None,
    task_ident="tid",
):
    dto = TaskResultDto(
        task_ident=task_ident,
        command=CommandDto("cmd", {}, CommandOptionsDto()),
        reports=list(reports),
        state=TaskState.FINISHED,
        task_finish_type=finish_type,
        kill_reason=kill_reason,
        result=result,
    )
    return json.dumps(to_dict(dto))


class _ServerThreadMixin:
    """Start a scriptable server in a background thread and tear it down."""

    def _start_server(self, server):
        self.server = server
        # A short poll interval keeps shutdown() fast; with the default of
        # 0.5s every test would spend up to half a second in teardown.
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self._thread.start()
        # Cleanups run LIFO, so register this first: it runs *after* the server
        # has stopped. The server is single-threaded, so by then no request is
        # in flight and the counters are final.
        self.addCleanup(self.assert_all_scripts_consumed)
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.wait_event.set()
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)

    def assert_all_scripts_consumed(self):
        self.assertEqual(
            self.server.unexpected_request_count,
            0,
            f"server received {self.server.unexpected_request_count} more "
            "request(s) than were scripted",
        )
        self.assertEqual(
            self.server.unused_script_count,
            0,
            f"{self.server.unused_script_count} scripted response(s) were "
            "never requested",
        )


class UnixSocketIntegrationBase(_ServerThreadMixin, TestCase):
    def setUp(self):
        # We deliberately don't want to use pcs_test.tools.misc.get_tmp_dir here
        # It creates the directory under pcs_test/resources/temp inside the
        # source tree, so the socket path length depends on where the repository
        # is checked out - we could exceed the limit for path length of AF_UNIX
        # socket (108 bytes), making these tests fail.
        self.tmp_dir = tempfile.mkdtemp(prefix="pcs-apiv2-")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        # Short socket name to stay well under the AF_UNIX path length limit.
        self.socket_path = os.path.join(self.tmp_dir, "s")
        self._start_server(
            _ScriptableUnixServer(self.socket_path, _RecordingHandler)
        )

        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.connector = ApiV2Connector.for_unix_socket(
            self.socket_path, self.logger, api_v2_timeout=TIMEOUT
        )


class HttpsIntegrationBase(_ServerThreadMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        # Generating an RSA key is the slow part, so do it once per class.
        cls._cert_dir = get_tmp_dir("tier0_api_v2_client_certs")
        cls.cert_path, cls.key_path = _write_self_signed_cert(
            cls._cert_dir.name
        )

    @classmethod
    def tearDownClass(cls):
        cls._cert_dir.cleanup()

    def setUp(self):
        _bypass_proxy_for_localhost(self)
        server = _ScriptableHttpsServer(("127.0.0.1", 0), _RecordingHandler)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(self.cert_path, self.key_path)
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        self.host, self.port = server.server_address
        self._start_server(server)

        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.connector = ApiV2Connector(
            self.logger,
            TcpTransport(self.host, self.port, TOKEN),
            api_v2_timeout=TIMEOUT,
        )


@mock.patch.object(client.time, "sleep")
class _RunCommandMixin:
    def test_full_round_trip(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("task-42"))
        self.server.add_response(
            200,
            _result_body(
                finish_type=TaskFinishType.SUCCESS, result={"out": "ok"}
            ),
        )

        results = list(
            self.connector.run_command("cluster.status", {"name": "c"})
        )

        self.assertEqual(results, [([], True, {"out": "ok"})])
        mock_sleep.assert_not_called()

        # The real transport/handler produced two HTTP requests on the socket.
        self.assertEqual(len(self.server.recorded_requests), 2)
        create_req, result_req = self.server.recorded_requests

        self.assertEqual(create_req.method, "POST")
        self.assertEqual(create_req.path, "/api/v2/task/create")
        self.assertEqual(
            create_req.headers.get("Content-Type"), "application/json"
        )
        sent_command = json.loads(create_req.body)
        self.assertEqual(sent_command["command_name"], "cluster.status")
        self.assertEqual(sent_command["params"], {"name": "c"})

        self.assertEqual(result_req.method, "GET")
        self.assertEqual(
            result_req.path, "/api/v2/task/result?task_ident=task-42"
        )
        self.assertEqual(result_req.body, "")

    def test_polls_until_finished(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.UNFINISHED)
        )
        self.server.add_response(
            200,
            _result_body(finish_type=TaskFinishType.SUCCESS, result="done"),
        )

        results = list(self.connector.run_command("some.cmd", {}))

        self.assertEqual(results, [([], True, "done")])
        mock_sleep.assert_called_once_with(1.0)
        # create + two result polls
        self.assertEqual(len(self.server.recorded_requests), 3)

    def test_kill_is_not_success(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(
            200,
            _result_body(
                finish_type=TaskFinishType.KILL,
                kill_reason=TaskKillReason.USER,
            ),
        )

        results = list(self.connector.run_command("some.cmd", {}))

        self.assertEqual(results, [([], False, None)])
        mock_sleep.assert_not_called()

    def test_fail_is_not_success(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.FAIL)
        )

        results = list(self.connector.run_command("some.cmd", {}))

        self.assertEqual(results, [([], False, None)])
        mock_sleep.assert_not_called()

    def test_http_error_while_polling(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.UNFINISHED)
        )
        self.server.add_response(500, json.dumps({"detail": "boom"}))

        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 500)
        mock_sleep.assert_called_once_with(1.0)

    def test_malformed_response_while_polling(self, mock_sleep):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(200, "this is not json")

        with self.assertRaises(ApiV2ProtocolError):
            list(self.connector.run_command("some.cmd", {}))
        mock_sleep.assert_not_called()

    def test_http_400_is_request_error(self, _):
        self.server.add_response(400, json.dumps({"detail": "bad payload"}))
        with self.assertRaises(ApiV2RequestError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.http_code, 400)

    def test_http_500_is_communication_error(self, _):
        self.server.add_response(500, json.dumps({"detail": "boom"}))
        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 500)

    def test_malformed_json_is_protocol_error(self, _):
        self.server.add_response(200, "this is not json")
        with self.assertRaises(ApiV2ProtocolError):
            list(self.connector.run_command("some.cmd", {}))


class UnixSocketRunCommand(_RunCommandMixin, UnixSocketIntegrationBase):
    pass


class HttpsRunCommand(_RunCommandMixin, HttpsIntegrationBase):
    def test_token_cookie_is_sent(self):
        self.server.add_response(200, _task_ident_body("tid"))
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.SUCCESS)
        )

        list(self.connector.run_command("some.cmd", {}))

        self.assertEqual(len(self.server.recorded_requests), 2)
        for request in self.server.recorded_requests:
            self.assertEqual(
                request.headers.get("Cookie"), f"token={TOKEN}", request.path
            )


class UnixConnectionError(TestCase):
    def test_missing_socket_is_connection_error(self):
        tmp_dir = tempfile.mkdtemp(prefix="pcs-apiv2-")
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        missing_path = os.path.join(tmp_dir, "does-not-exist")

        logger = mock.MagicMock(spec_set=logging.Logger)
        connector = ApiV2Connector.for_unix_socket(
            missing_path, logger, api_v2_timeout=TIMEOUT
        )

        with self.assertRaises(ApiV2ConnectionError) as cm:
            list(connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.endpoint, missing_path)


class HttpsConnectionError(TestCase):
    def test_refused_connection_is_connection_error(self):
        _bypass_proxy_for_localhost(self)
        # Bind then immediately close a socket to obtain a port nothing listens
        # on, so the connection attempt is refused.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        _, port = probe.getsockname()
        probe.close()

        logger = mock.MagicMock(spec_set=logging.Logger)
        connector = ApiV2Connector(
            logger,
            TcpTransport("127.0.0.1", port, TOKEN),
            api_v2_timeout=TIMEOUT,
        )
        with self.assertRaises(ApiV2ConnectionError) as cm:
            list(connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.endpoint, f"127.0.0.1:{port}")


class UnixSocketTimeout(UnixSocketIntegrationBase):
    def test_slow_response_times_out(self):
        self.connector = ApiV2Connector.for_unix_socket(
            self.socket_path, self.logger, api_v2_timeout=0.1
        )
        self.server.add_response(200, _task_ident_body("tid"), delay=5.0)
        with self.assertRaises(ApiV2TimeoutError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.timeout, 0.1)
        # The client may give up before the server thread got to the request.
        # Make sure the server has handled it before teardown shuts it down,
        # otherwise the scripted response could remain unused.
        self.assertTrue(
            self.server.response_picked_event.wait(5),
            "server did not receive the request",
        )
