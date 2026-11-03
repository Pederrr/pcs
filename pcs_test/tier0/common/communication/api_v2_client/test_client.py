import json
import logging
from unittest import TestCase, mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

from pcs.common.async_tasks.dto import (
    CommandDto,
    CommandOptionsDto,
    TaskIdentDto,
    TaskResultDto,
)
from pcs.common.async_tasks.types import (
    TaskFinishType,
    TaskKillReason,
    TaskState,
)
from pcs.common.communication.api_v2_client import client
from pcs.common.communication.api_v2_client.client import (
    ApiV2Connector,
    _ApiV2Client,
    _request_data,
)
from pcs.common.communication.api_v2_client.errors import (
    ApiV2CommunicationError,
    ApiV2ConnectionError,
    ApiV2ProtocolError,
    ApiV2RequestError,
    ApiV2TimeoutError,
)
from pcs.common.communication.api_v2_client.transport import Transport
from pcs.common.interface.dto import to_dict
from pcs.common.reports import (
    ReportItem,
    ReportItemContext,
    messages,
)

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


def _log_finished(url, response_code, response_data):
    return mock.call.debug(
        "Finished calling: %s\nResponse code: %s\n"
        "--Debug Response Start--\n%s\n--Debug Response End--",
        url,
        response_code,
        response_data,
    )


def _log_task_created(task_ident, target):
    return mock.call.debug("Created task '%s' on %s", task_ident, target)


def _log_task_status(task_ident, finish_type, detail=""):
    return mock.call.debug(
        "Task '%s' status: %s%s",
        task_ident,
        finish_type,
        f" ({detail})" if detail else "",
    )


class _FakeResponse:
    """Minimal stand-in for the context manager returned by Transport.open."""

    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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


def _result_body(
    *,
    finish_type=TaskFinishType.SUCCESS,
    reports=(),
    result=None,
    kill_reason=None,
    task_ident="tid",
):
    command = CommandDto("cmd", {}, CommandOptionsDto())
    dto = TaskResultDto(
        task_ident=task_ident,
        command=command,
        reports=list(reports),
        state=TaskState.FINISHED,
        task_finish_type=finish_type,
        kill_reason=kill_reason,
        result=result,
    )
    return json.dumps(to_dict(dto))


def _report_dto(target="t", data="d"):
    return ReportItem.info(
        messages.NodeCommunicationStarted(target, data)
    ).to_dto()


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


class SendRequestSuccess(SendRequestBase):
    def test_returns_body_and_logs(self):
        self.transport.open_mock.return_value = _FakeResponse("payload", 200)

        self.assertEqual("payload", self.client._send_request(self.request))

        self.transport.open_mock.assert_called_once_with(self.request, TIMEOUT)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                _log_finished(self.full_url, 200, "payload"),
            ],
        )


class SendRequestHttpError(SendRequestBase):
    def _raise(self, code, reason="Bad Request"):
        self.transport.open_mock.side_effect = HTTPError(
            self.full_url, code, reason, {}, None
        )

    def test_400_with_flag_is_request_error(self):
        self._raise(400)
        with self.assertRaises(ApiV2RequestError) as cm:
            self.client._send_request(self.request, request_error_on_400=True)
        self.assertEqual(cm.exception.endpoint, LABEL)
        self.assertEqual(cm.exception.http_code, 400)
        self.assertEqual(cm.exception.reason, "Bad Request")
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Error calling %s: HTTP error: %s %s",
                    self.full_url,
                    400,
                    "Bad Request",
                ),
            ],
        )

    def test_400_without_flag_is_communication_error(self):
        self._raise(400)
        with self.assertRaises(ApiV2CommunicationError) as cm:
            self.client._send_request(self.request)
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 400)
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Error calling %s: HTTP error: %s %s",
                    self.full_url,
                    400,
                    "Bad Request",
                ),
            ],
        )

    def test_500_is_communication_error_even_with_flag(self):
        self._raise(500, "Server Error")
        with self.assertRaises(ApiV2CommunicationError) as cm:
            self.client._send_request(self.request, request_error_on_400=True)
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 500)
        self.assertEqual(cm.exception.reason, "Server Error")
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(self.full_url),
                mock.call.debug(
                    "Error calling %s: HTTP error: %s %s",
                    self.full_url,
                    500,
                    "Server Error",
                ),
            ],
        )


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


class ParseResponse(TestCase):
    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()
        self.client = _ApiV2Client(self.transport, self.logger)

    def test_valid(self):
        result = self.client._parse_response(
            json.dumps({"task_ident": "abc"}), TaskIdentDto
        )
        self.assertEqual(result, TaskIdentDto("abc"))

    def test_invalid_json(self):
        with self.assertRaises(ApiV2ProtocolError) as cm:
            self.client._parse_response("not json", TaskIdentDto)
        self.assertEqual(cm.exception.endpoint, LABEL)
        self.assertEqual(
            self.logger.mock_calls,
            [
                mock.call.debug(
                    "Response from %s does not conform to %s: %s",
                    LABEL,
                    "TaskIdentDto",
                    "Expecting value: line 1 column 1 (char 0)",
                )
            ],
        )

    def test_schema_mismatch(self):
        with self.assertRaises(ApiV2ProtocolError):
            self.client._parse_response(
                json.dumps({"wrong_key": "x"}), TaskIdentDto
            )


class TaskCreate(TestCase):
    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()
        self.client = _ApiV2Client(
            self.transport, self.logger, api_v2_timeout=TIMEOUT
        )
        self.command = CommandDto(
            "cluster.setup", {"name": "c"}, CommandOptionsDto()
        )

    def test_success(self):
        self.transport.open_mock.return_value = _FakeResponse(
            json.dumps({"task_ident": "task-1"})
        )

        self.assertEqual("task-1", self.client.task_create(self.command))

        # The command is serialized into the request body. Request URL/method
        # construction is covered by test_transport.py.
        request = self.transport.open_mock.call_args[0][0]
        self.assertEqual(json.loads(request.data), to_dict(self.command))
        create_url = f"{BASE_URL}/api/v2/task/create"
        self.assertEqual(
            self.logger.mock_calls,
            [
                _log_started(create_url, json.dumps(to_dict(self.command))),
                _log_finished(
                    create_url, 200, json.dumps({"task_ident": "task-1"})
                ),
                _log_task_created("task-1", LABEL),
            ],
        )

    def test_http_400_is_request_error(self):
        self.transport.open_mock.side_effect = HTTPError(
            "url", 400, "Bad Request", {}, None
        )
        with self.assertRaises(ApiV2RequestError):
            self.client.task_create(self.command)


@mock.patch.object(client.time, "sleep")
class TaskResult(TestCase):
    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()
        self.client = _ApiV2Client(
            self.transport, self.logger, api_v2_timeout=TIMEOUT
        )

    def fixture_poll_log_calls(
        self, task_finish_type=TaskFinishType.UNFINISHED, task_finish_detail=""
    ):
        result_url = f"{BASE_URL}/api/v2/task/result?task_ident=tid"
        return [
            _log_started(result_url),
            # The response body varies per poll; its exact text is not the
            # subject of this assertion.
            _log_finished(result_url, 200, mock.ANY),
            _log_task_status("tid", task_finish_type, task_finish_detail),
        ]

    def test_unfinished_without_reports_then_success(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(_result_body(finish_type=TaskFinishType.UNFINISHED)),
            _FakeResponse(_result_body(finish_type=TaskFinishType.UNFINISHED)),
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.SUCCESS, result={"a": 1}
                )
            ),
        ]

        results = list(self.client.task_result("tid", poll_interval=0.5))

        # Client yields only when there is new data to report, so only the
        # final result will be yielded in this case
        self.assertEqual(results, [([], True, {"a": 1})])
        self.assertEqual(2, mock_sleep.call_count)
        mock_sleep.assert_has_calls([mock.call(0.5), mock.call(0.5)])

        self.assertEqual(
            self.logger.mock_calls,
            [
                *self.fixture_poll_log_calls(),
                *self.fixture_poll_log_calls(),
                *self.fixture_poll_log_calls(
                    task_finish_type=TaskFinishType.SUCCESS,
                    task_finish_detail="{'a': 1}",
                ),
            ],
        )

    def test_incremental_reports_are_deduplicated(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.UNFINISHED,
                    reports=[_report_dto("a")],
                )
            ),
            _FakeResponse(  # new reports
                _result_body(
                    finish_type=TaskFinishType.UNFINISHED,
                    reports=[_report_dto("a"), _report_dto("b")],
                )
            ),
            _FakeResponse(  # nothing new
                _result_body(
                    finish_type=TaskFinishType.UNFINISHED,
                    reports=[_report_dto("a"), _report_dto("b")],
                )
            ),
            _FakeResponse(  # result
                _result_body(
                    finish_type=TaskFinishType.SUCCESS,
                    reports=[
                        _report_dto("a"),
                        _report_dto("b"),
                        _report_dto("c"),
                    ],
                    result="done",
                )
            ),
        ]

        results = list(self.client.task_result("tid", poll_interval=0.5))

        # Client yields only when there is new data to report
        # The response with no new data will not be yielded, so only 3 results
        # even when we polled 4 times
        self.assertEqual(len(results), 3)
        self.assertEqual(3, mock_sleep.call_count)
        mock_sleep.assert_has_calls(
            [mock.call(0.5), mock.call(0.5), mock.call(0.5)]
        )

        first_reports, first_success, first_data = results[0]
        self.assertEqual(
            [r.to_dto() for r in first_reports], [_report_dto("a")]
        )
        self.assertFalse(first_success)
        self.assertIsNone(first_data)

        second_reports, second_success, second_data = results[1]
        # Only the new (second) report is returned, not the already-seen one.
        self.assertEqual(
            [r.to_dto() for r in second_reports], [_report_dto("b")]
        )
        self.assertFalse(second_success)
        self.assertIsNone(second_data)

        third_reports, third_success, third_data = results[2]
        self.assertEqual(
            [r.to_dto() for r in third_reports], [_report_dto("c")]
        )
        self.assertTrue(third_success)
        self.assertEqual(third_data, "done")

        self.assertEqual(
            self.logger.mock_calls,
            [
                *self.fixture_poll_log_calls(),
                *self.fixture_poll_log_calls(),
                *self.fixture_poll_log_calls(),
                *self.fixture_poll_log_calls(
                    task_finish_type=TaskFinishType.SUCCESS,
                    task_finish_detail="done",
                ),
            ],
        )

    def test_kill_is_not_success(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.KILL,
                    kill_reason=TaskKillReason.USER,
                )
            ),
        ]

        results = list(self.client.task_result("tid"))

        self.assertEqual(results, [([], False, None)])
        mock_sleep.assert_not_called()
        self.assertEqual(
            self.logger.mock_calls,
            self.fixture_poll_log_calls(
                task_finish_type=TaskFinishType.KILL,
                task_finish_detail="kill reason: USER",
            ),
        )

    def test_kill_no_kill_reason(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(_result_body(finish_type=TaskFinishType.KILL)),
        ]

        results = list(self.client.task_result("tid"))

        self.assertEqual(results, [([], False, None)])
        mock_sleep.assert_not_called()
        self.assertEqual(
            self.logger.mock_calls,
            self.fixture_poll_log_calls(task_finish_type=TaskFinishType.KILL),
        )

    def test_fail_is_not_success(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.FAIL,
                    reports=[_report_dto("a")],
                )
            ),
        ]

        results = list(self.client.task_result("tid"))

        self.assertEqual(len(results), 1)
        reports, success, data = results[0]
        self.assertEqual([r.to_dto() for r in reports], [_report_dto("a")])
        self.assertFalse(success)
        self.assertIsNone(data)
        mock_sleep.assert_not_called()
        self.assertEqual(
            self.logger.mock_calls,
            self.fixture_poll_log_calls(task_finish_type=TaskFinishType.FAIL),
        )

    def test_unhandled_exception_is_not_success(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(finish_type=TaskFinishType.UNHANDLED_EXCEPTION)
            ),
        ]

        results = list(self.client.task_result("tid"))

        self.assertEqual(results, [([], False, None)])
        mock_sleep.assert_not_called()
        self.assertEqual(
            self.logger.mock_calls,
            self.fixture_poll_log_calls(
                task_finish_type=TaskFinishType.UNHANDLED_EXCEPTION
            ),
        )

    def test_http_error_while_polling(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.UNFINISHED,
                    reports=[_report_dto("a")],
                )
            ),
            HTTPError("url", 500, "Server Error", {}, None),
        ]

        iterator = self.client.task_result("tid", poll_interval=0.5)

        # Reports received before the failure are delivered to the caller
        reports, success, data = next(iterator)
        self.assertEqual([r.to_dto() for r in reports], [_report_dto("a")])
        self.assertFalse(success)
        self.assertIsNone(data)
        # The next poll fails and the error is propagated
        with self.assertRaises(ApiV2CommunicationError) as cm:
            next(iterator)
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 500)
        mock_sleep.assert_called_once_with(0.5)

    def test_http_400_while_polling_is_not_request_error(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            HTTPError("url", 400, "Bad Request", {}, None),
        ]

        with self.assertRaises(ApiV2CommunicationError) as cm:
            list(self.client.task_result("tid"))
        self.assertNotIsInstance(cm.exception, ApiV2RequestError)
        self.assertEqual(cm.exception.http_code, 400)
        mock_sleep.assert_not_called()

    def test_malformed_response_while_polling(self, mock_sleep):
        self.transport.open_mock.side_effect = [
            _FakeResponse(_result_body(finish_type=TaskFinishType.UNFINISHED)),
            _FakeResponse(json.dumps({"task_ident": "tid"})),
        ]

        with self.assertRaises(ApiV2ProtocolError) as cm:
            list(self.client.task_result("tid", poll_interval=0.5))
        self.assertEqual(cm.exception.endpoint, LABEL)
        mock_sleep.assert_called_once_with(0.5)


@mock.patch.object(client.time, "sleep")
class TaskResultContext(TestCase):
    """
    The reports yielded to the caller carry the context node name the client
    was created with, so callers can tell which node a report came from.
    """

    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()

    def _make_client(self, context_node_name=None):
        return _ApiV2Client(
            self.transport,
            self.logger,
            context_node_name=context_node_name,
            api_v2_timeout=TIMEOUT,
        )

    def _run(self, context_node_name):
        self.transport.open_mock.side_effect = [
            _FakeResponse(
                _result_body(
                    finish_type=TaskFinishType.SUCCESS,
                    reports=[_report_dto("a"), _report_dto("b")],
                    result="done",
                )
            ),
        ]
        client_ = self._make_client(context_node_name)
        results = list(client_.task_result("tid"))
        (new_reports, _success, _data) = results[0]
        return new_reports

    def test_context_node_is_attached_to_yielded_reports(self, _mock_sleep):
        new_reports = self._run(context_node_name="node1")

        self.assertEqual(len(new_reports), 2)
        self.assertTrue(
            all(
                report.context == ReportItemContext("node1")
                for report in new_reports
            )
        )

    def test_no_context_node_leaves_yielded_reports_without_context(
        self, _mock_sleep
    ):
        new_reports = self._run(context_node_name=None)

        self.assertEqual(len(new_reports), 2)
        self.assertTrue(all(report.context is None for report in new_reports))


class RunCommand(TestCase):
    def setUp(self):
        self.logger = mock.MagicMock(spec_set=logging.Logger)
        self.transport = _FakeTransport()

    @mock.patch.object(client, "_ApiV2Client", autospec=True)
    def test_builds_command_and_delegates(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.task_create.return_value = "task-9"
        sentinel = object()
        mock_client.task_result.return_value = sentinel

        connector = ApiV2Connector(
            self.logger,
            self.transport,
            effective_username="hacluster",
            effective_groups=["haclient"],
            request_timeout=120,
        )
        result = connector.run_command(
            "cluster.setup", {"name": "c"}, poll_interval=2.0
        )

        self.assertIs(result, sentinel)
        mock_client.task_create.assert_called_once_with(
            CommandDto(
                command_name="cluster.setup",
                params={"name": "c"},
                options=CommandOptionsDto(
                    request_timeout=120,
                    effective_username="hacluster",
                    effective_groups=["haclient"],
                ),
            )
        )
        mock_client.task_result.assert_called_once_with("task-9", 2.0)
