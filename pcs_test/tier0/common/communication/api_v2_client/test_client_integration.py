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

from pcs.common import reports
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
from pcs.common.communication.api_v2_client.transport import (
    TcpTransport,
    UnixSocketTransport,
)
from pcs.common.interface.dto import to_dict

from pcs_test.tools.misc import get_tmp_dir

TIMEOUT = 30.0
TOKEN = "secret-token"
TASK_IDENT = "tid"


def _log_started(url, data=""):
    if not data:
        return (logging.DEBUG, f"Sending HTTP request to: {url}")
    return (
        logging.DEBUG,
        f"Sending HTTP request to: {url}\n"
        f"--Debug Input Start--\n{data}\n--Debug Input End--",
    )


def _log_finished(url, response_code, response_data):
    return (
        logging.DEBUG,
        f"Finished calling: {url}\n"
        f"Response code: {response_code}\n"
        f"--Debug Response Start--\n{response_data}\n--Debug Response End--",
    )


def _log_http_error(url, code, reason):
    return (logging.DEBUG, f"Error calling {url}: HTTP error: {code} {reason}")


def _log_task_created(task_ident, target):
    return (logging.DEBUG, f"Created task '{task_ident}' on {target}")


def _log_task_status(task_ident, finish_type, detail=""):
    if not detail:
        return (logging.DEBUG, f"Task '{task_ident}' status: {finish_type}")
    return (
        logging.DEBUG,
        f"Task '{task_ident}' status: {finish_type} ({detail})",
    )


def _log_not_conform(target, dto_name, detail):
    return (
        logging.DEBUG,
        f"Response from {target} does not conform to {dto_name}: {detail}",
    )


def _log_unable_to_connect(target, url, reason):
    return (
        logging.DEBUG,
        f"Unable to connect to {target} when calling {url}: {reason}",
    )


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


def _isolate_proxy_env(test_case):
    """
    Make the tests independent of proxy settings in the environment.

    Make urllib connect to the local test server directly even if a proxy is
    configured in the environment (https_proxy, all_proxy). Otherwise HTTPS
    requests to 127.0.0.1 would be sent to the proxy and the tests would fail.
    Also unset the proxy variables, so that the client does not log a proxy
    warning on connection errors and the logged messages are deterministic.
    """
    patcher = mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1"})
    patcher.start()
    test_case.addCleanup(patcher.stop)
    for var in ("https_proxy", "all_proxy"):
        os.environ.pop(var, None)
        os.environ.pop(var.upper(), None)


def _create_body(command_name, params):
    return json.dumps(
        to_dict(CommandDto(command_name, params, CommandOptionsDto()))
    )


def _task_ident_body():
    return json.dumps({"task_ident": TASK_IDENT})


def _result_body(
    *,
    finish_type=TaskFinishType.SUCCESS,
    reports=(),
    result=None,
    kill_reason=None,
):
    dto = TaskResultDto(
        task_ident=TASK_IDENT,
        command=CommandDto("cmd", {}, CommandOptionsDto()),
        reports=list(reports),
        state=TaskState.FINISHED,
        task_finish_type=finish_type,
        kill_reason=kill_reason,
        result=result,
    )
    return json.dumps(to_dict(dto))


def _report_dto(target="t", data="d"):
    return reports.ReportItem.info(
        reports.messages.NodeCommunicationStarted(target, data)
    ).to_dto()


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
            if self.server.wait_event.is_set():
                # The server is shutting down, the client has already given
                # up and closed the connection. Do not write the response:
                # writing to a closed socket raises SIGPIPE, which kills the
                # whole test process
                return
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


class _LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


class _LogCaptureMixin:
    """
    Provide a real logger for the client and capture what it logs. Using a real
    logger (rather than a mock) checks the messages as they are actually
    rendered, i.e. that the format strings and their arguments match.
    """

    def _setup_logger(self):
        self.logger = logging.getLogger(f"pcs_test.api_v2_client.{self.id()}")
        self.logger.setLevel(logging.DEBUG)
        # Do not pass the records to the root logger, keep the test output clean
        self.logger.propagate = False
        self.log_handler = _LogCaptureHandler()
        self.logger.addHandler(self.log_handler)
        self.addCleanup(self.logger.removeHandler, self.log_handler)

    def assert_logs(self, expected_logs):
        self.assertEqual(self.log_handler.records, expected_logs)


class UnixSocketIntegrationBase(_LogCaptureMixin, _ServerThreadMixin, TestCase):
    def setUp(self):
        _isolate_proxy_env(self)
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

        self.label = self.socket_path
        self.base_url = "unix://localhost"
        self._setup_logger()
        self.connector = self._make_connector()

    def _make_connector(
        self, context_node_name=None, api_v2_timeout=TIMEOUT, **kwargs
    ):
        return ApiV2Connector(
            self.logger,
            UnixSocketTransport(self.socket_path),
            context_node_name,
            api_v2_timeout=api_v2_timeout,
            **kwargs,
        )


class HttpsIntegrationBase(_LogCaptureMixin, _ServerThreadMixin, TestCase):
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
        _isolate_proxy_env(self)
        server = _ScriptableHttpsServer(("127.0.0.1", 0), _RecordingHandler)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(self.cert_path, self.key_path)
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        self.host, self.port = server.server_address
        self._start_server(server)

        self.label = f"{self.host}:{self.port}"
        self.base_url = f"https://{self.host}:{self.port}"
        self._setup_logger()
        self.connector = self._make_connector()

    def _make_connector(
        self, context_node_name=None, api_v2_timeout=TIMEOUT, **kwargs
    ):
        return ApiV2Connector(
            self.logger,
            TcpTransport(self.host, self.port, TOKEN),
            context_node_name,
            api_v2_timeout=api_v2_timeout,
            **kwargs,
        )


@mock.patch.object(client.time, "sleep")
class _RunCommandMixin:
    @property
    def create_url(self):
        return f"{self.base_url}/api/v2/task/create"

    @property
    def result_url(self):
        return f"{self.base_url}/api/v2/task/result?task_ident={TASK_IDENT}"

    def fixture_create_logs(self, command_name="some.cmd", params=None):
        return [
            _log_started(
                self.create_url, _create_body(command_name, params or {})
            ),
            _log_finished(self.create_url, 200, _task_ident_body()),
            _log_task_created(TASK_IDENT, self.label),
        ]

    def fixture_poll_logs(self, response_body, finish_type, detail=""):
        return [
            _log_started(self.result_url),
            _log_finished(self.result_url, 200, response_body),
            _log_task_status(TASK_IDENT, finish_type, detail),
        ]

    def assert_client_results(self, results, expected_results):
        self.assertEqual(
            [
                ([r.to_dto() for r in reports], success, data)
                for reports, success, data in results
            ],
            expected_results,
        )

    def assert_poll_sleep(self, mock_sleep, count, timeout=1.0):
        self.assertEqual(
            mock_sleep.call_args_list, [mock.call(timeout)] * count
        )

    def _assert_task_not_success(
        self, mock_sleep, finish_type, kill_reason=None, detail=""
    ):
        result_body = _result_body(
            finish_type=finish_type, kill_reason=kill_reason
        )
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(200, result_body)

        results = list(self.connector.run_command("some.cmd", {}))

        self.assert_client_results(results, [([], False, None)])
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                *self.fixture_poll_logs(result_body, finish_type, detail),
            ]
        )

    def test_simple_round_trip(self, mock_sleep):
        result_body = _result_body(
            finish_type=TaskFinishType.SUCCESS, result={"out": "ok"}
        )
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(200, result_body)

        results = list(
            self.connector.run_command("cluster.status", {"name": "c"})
        )

        # The real transport/handler produced two HTTP requests on the socket.
        self.assertEqual(len(self.server.recorded_requests), 2)
        create_req, result_req = self.server.recorded_requests

        # assert that the requests are as expected
        self.assertEqual(create_req.method, "POST")
        self.assertEqual(create_req.path, "/api/v2/task/create")
        self.assertEqual(
            create_req.headers.get("Content-Type"), "application/json"
        )
        sent_command = json.loads(create_req.body)
        self.assertEqual(sent_command["command_name"], "cluster.status")
        self.assertEqual(sent_command["params"], {"name": "c"})

        self.assertEqual(result_req.method, "GET")
        self.assertEqual(result_req.path, "/api/v2/task/result?task_ident=tid")
        self.assertEqual(result_req.body, "")

        # assert that client handles responses as expected
        self.assert_client_results(results, [([], True, {"out": "ok"})])
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs("cluster.status", {"name": "c"}),
                *self.fixture_poll_logs(
                    result_body, TaskFinishType.SUCCESS, "{'out': 'ok'}"
                ),
            ]
        )

    def test_polls_until_finished(self, mock_sleep):
        self.server.add_response(200, _task_ident_body())
        poll_bodies = [
            _result_body(
                finish_type=TaskFinishType.UNFINISHED,
                reports=[_report_dto("a")],
            ),
            _result_body(
                finish_type=TaskFinishType.UNFINISHED,
                reports=[_report_dto("a"), _report_dto("b")],
            ),
            _result_body(
                finish_type=TaskFinishType.UNFINISHED,
                reports=[_report_dto("a"), _report_dto("b")],
            ),
        ]
        final_body = _result_body(
            finish_type=TaskFinishType.SUCCESS,
            reports=[_report_dto("a"), _report_dto("b"), _report_dto("c")],
            result="done",
        )
        for body in poll_bodies:
            self.server.add_response(200, body)
        self.server.add_response(200, final_body)

        results = list(self.connector.run_command("some.cmd", {}))

        # 1 create + 4 result polls
        self.assertEqual(len(self.server.recorded_requests), 5)

        # Client yields only when there is new data to report
        # The response with no new data will not be yielded, so only 3 results
        # even when we polled 4 times
        self.assert_client_results(
            results,
            [
                ([_report_dto("a")], False, None),
                ([_report_dto("b")], False, None),
                ([_report_dto("c")], True, "done"),
            ],
        )
        self.assert_poll_sleep(mock_sleep, 3)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                *[
                    log
                    for body in poll_bodies
                    for log in self.fixture_poll_logs(
                        body, TaskFinishType.UNFINISHED
                    )
                ],
                *self.fixture_poll_logs(
                    final_body, TaskFinishType.SUCCESS, "done"
                ),
            ]
        )

    def test_kill_is_not_success(self, mock_sleep):
        self._assert_task_not_success(
            mock_sleep,
            TaskFinishType.KILL,
            kill_reason=TaskKillReason.USER,
            detail="kill reason: USER",
        )

    def test_kill_no_kill_reason(self, mock_sleep):
        self._assert_task_not_success(mock_sleep, TaskFinishType.KILL)

    def test_fail_is_not_success(self, mock_sleep):
        self._assert_task_not_success(mock_sleep, TaskFinishType.FAIL)

    def test_unhandled_exception_is_not_success(self, mock_sleep):
        self._assert_task_not_success(
            mock_sleep, TaskFinishType.UNHANDLED_EXCEPTION
        )

    def test_http_error_while_polling(self, mock_sleep):
        error_body = json.dumps({"detail": "boom"})
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(500, error_body)

        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 500)
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                _log_started(self.result_url),
                _log_http_error(self.result_url, 500, "Internal Server Error"),
            ]
        )

    def test_http_400_while_polling_is_not_request_error(self, mock_sleep):
        # Only HTTP 400 from task/create means the request payload is invalid
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(400, json.dumps({"detail": "bad"}))

        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.http_code, 400)
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                _log_started(self.result_url),
                _log_http_error(self.result_url, 400, "Bad Request"),
            ]
        )

    def test_malformed_response_while_polling(self, mock_sleep):
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(200, "this is not json")

        with self.assertRaises(ApiV2ProtocolError):
            list(self.connector.run_command("some.cmd", {}))
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                _log_started(self.result_url),
                _log_finished(self.result_url, 200, "this is not json"),
                _log_not_conform(
                    self.label,
                    "TaskResultDto",
                    "Expecting value: line 1 column 1 (char 0)",
                ),
            ]
        )

    def test_malformed_response_while_polling_bad_schema(self, mock_sleep):
        bad_body = json.dumps({"wrong_key": "x"})
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(200, bad_body)

        with self.assertRaises(ApiV2ProtocolError):
            list(self.connector.run_command("some.cmd", {}))
        self.assert_poll_sleep(mock_sleep, 0)
        self.assert_logs(
            [
                *self.fixture_create_logs(),
                _log_started(self.result_url),
                _log_finished(self.result_url, 200, bad_body),
                _log_not_conform(
                    self.label,
                    "TaskResultDto",
                    'missing value for field "task_ident"',
                ),
            ]
        )

    def test_http_400_is_request_error(self, _mock_sleep):
        self.server.add_response(400, json.dumps({"detail": "bad payload"}))

        with self.assertRaises(ApiV2RequestError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.http_code, 400)
        self.assert_logs(
            [
                _log_started(self.create_url, _create_body("some.cmd", {})),
                _log_http_error(self.create_url, 400, "Bad Request"),
            ]
        )

    def test_http_500_is_communication_error(self, _mock_sleep):
        self.server.add_response(500, json.dumps({"detail": "boom"}))

        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.http_code, 500)
        self.assert_logs(
            [
                _log_started(self.create_url, _create_body("some.cmd", {})),
                _log_http_error(self.create_url, 500, "Internal Server Error"),
            ]
        )

    def test_malformed_json_is_protocol_error(self, _mock_sleep):
        self.server.add_response(200, "this is not json")

        with self.assertRaises(ApiV2ProtocolError):
            list(self.connector.run_command("some.cmd", {}))
        self.assert_logs(
            [
                _log_started(self.create_url, _create_body("some.cmd", {})),
                _log_finished(self.create_url, 200, "this is not json"),
                _log_not_conform(
                    self.label,
                    "TaskIdentDto",
                    "Expecting value: line 1 column 1 (char 0)",
                ),
            ]
        )

    def test_context_node_is_attached_to_yielded_reports(self, _mock_sleep):
        # The reports yielded to the caller carry the context node name the
        # connector was created with, so callers can tell which node a report
        # came from.
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(
            200,
            _result_body(
                finish_type=TaskFinishType.SUCCESS,
                reports=[_report_dto("a"), _report_dto("b")],
            ),
        )
        connector = self._make_connector(context_node_name="node1")

        results = list(connector.run_command("some.cmd", {}))

        self.assertEqual(len(results), 1)
        new_reports, _, _ = results[0]
        self.assertEqual(
            [report.context for report in new_reports],
            [reports.ReportItemContext("node1")] * 2,
        )

    def test_all_command_options_are_sent(self, _mock_sleep):
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.SUCCESS)
        )
        connector = self._make_connector(
            effective_username="alice",
            effective_groups=["haclient", "wheel"],
            request_timeout=42,
        )

        list(connector.run_command("some.cmd", {"a": "b"}))

        self.assertEqual(len(self.server.recorded_requests), 2)
        create_req = self.server.recorded_requests[0]
        self.assertEqual(
            json.loads(create_req.body),
            to_dict(
                CommandDto(
                    "some.cmd",
                    {"a": "b"},
                    CommandOptionsDto(
                        request_timeout=42,
                        effective_username="alice",
                        effective_groups=["haclient", "wheel"],
                    ),
                )
            ),
        )


class UnixSocketRunCommand(_RunCommandMixin, UnixSocketIntegrationBase):
    pass


class HttpsRunCommand(_RunCommandMixin, HttpsIntegrationBase):
    def test_token_cookie_is_sent(self):
        self.server.add_response(200, _task_ident_body())
        self.server.add_response(
            200, _result_body(finish_type=TaskFinishType.SUCCESS)
        )

        list(self.connector.run_command("some.cmd", {}))

        self.assertEqual(len(self.server.recorded_requests), 2)
        for request in self.server.recorded_requests:
            self.assertEqual(
                request.headers.get("Cookie"), f"token={TOKEN}", request.path
            )


class UnixConnectionError(_LogCaptureMixin, TestCase):
    def test_missing_socket_is_connection_error(self):
        _isolate_proxy_env(self)
        self._setup_logger()
        tmp_dir = get_tmp_dir("pcs-apiv2-missing-unix-socket")
        self.addCleanup(tmp_dir.cleanup)
        missing_path = os.path.join(tmp_dir.name, "does-not-exist")

        connector = ApiV2Connector.for_unix_socket(
            missing_path, self.logger, api_v2_timeout=TIMEOUT
        )

        with self.assertRaises(ApiV2ConnectionError) as cm:
            list(connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.endpoint, missing_path)
        create_url = "unix://localhost/api/v2/task/create"
        self.assert_logs(
            [
                _log_started(create_url, _create_body("some.cmd", {})),
                _log_unable_to_connect(
                    missing_path,
                    create_url,
                    "[Errno 2] No such file or directory",
                ),
            ]
        )


class HttpsConnectionError(_LogCaptureMixin, TestCase):
    def test_refused_connection_is_connection_error(self):
        _isolate_proxy_env(self)
        self._setup_logger()
        # Bind then immediately close a socket to obtain a port nothing listens
        # on, so the connection attempt is refused.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        _, port = probe.getsockname()
        probe.close()

        connector = ApiV2Connector(
            self.logger,
            TcpTransport("127.0.0.1", port, TOKEN),
            api_v2_timeout=TIMEOUT,
        )
        with self.assertRaises(ApiV2ConnectionError) as cm:
            list(connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.endpoint, f"127.0.0.1:{port}")
        create_url = f"https://127.0.0.1:{port}/api/v2/task/create"
        self.assert_logs(
            [
                _log_started(create_url, _create_body("some.cmd", {})),
                _log_unable_to_connect(
                    f"127.0.0.1:{port}",
                    create_url,
                    "[Errno 111] Connection refused",
                ),
            ]
        )


class UnixSocketTimeout(UnixSocketIntegrationBase):
    def test_slow_response_times_out(self):
        connector = self._make_connector(api_v2_timeout=0.1)
        self.server.add_response(200, _task_ident_body(), delay=5.0)

        with self.assertRaises(ApiV2TimeoutError) as cm:
            list(connector.run_command("some.cmd", {}))
        self.assertEqual(cm.exception.timeout, 0.1)
        # The client may give up before the server thread got to the request.
        # Make sure the server has handled it before teardown shuts it down,
        # otherwise the scripted response could remain unused.
        self.assertTrue(
            self.server.response_picked_event.wait(5),
            "server did not receive the request",
        )
        create_url = f"{self.base_url}/api/v2/task/create"
        self.assert_logs(
            [
                _log_started(create_url, _create_body("some.cmd", {})),
                (logging.DEBUG, f"Calling {create_url} timed out: timed out"),
            ]
        )
