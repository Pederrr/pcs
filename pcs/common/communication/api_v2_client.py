import json
import os
import socket
import ssl
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from http.client import HTTPConnection, HTTPResponse
from ipaddress import AddressValueError, IPv6Address
from logging import Logger
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import AbstractHTTPHandler, Request, build_opener, urlopen

from pcs import settings
from pcs.common.async_tasks.dto import (
    CommandDto,
    CommandOptionsDto,
    TaskIdentDto,
    TaskResultDto,
)
from pcs.common.async_tasks.types import TaskFinishType
from pcs.common.interface.dto import DataTransferObject, from_dict, to_dict
from pcs.common.reports import (
    ReportItemContext,
    ReportItemList,
    report_dto_to_item,
)
from pcs.lib.external import is_proxy_set

# There can be self signed certificates.
_UNVERIFIED_SSL_CONTEXT = ssl._create_unverified_context()  # noqa: SLF001

_DTO = TypeVar("_DTO", bound=DataTransferObject)


def _format_addr_for_url(addr: str) -> str:
    try:
        IPv6Address(addr)
        return f"[{addr}]"
    except AddressValueError:
        return addr


class ApiV2CommunicationError(Exception):
    pass


class ApiV2RequestError(Exception):
    """The request payload is invalid; retrying on another node won't help."""


class _UnixSocketHttpConnection(HTTPConnection):
    """
    HTTP connection over a Unix domain socket instead of a TCP socket.
    """

    def __init__(
        self,
        host: str,
        socket_path: str | None = None,
        **kwargs: Any,
    ):
        # `host` is required by `HTTPConnection` (it ends up in the `Host`
        # header) but the actual endpoint is the Unix socket at `socket_path`.
        super().__init__(host, **kwargs)

        # We have to provide default value for `socket_path` variable to make
        # this class work properly with urllib. But we always want the path
        # to be present
        if socket_path is None:
            raise ValueError(
                "`socket_path` is required for unix socket connections"
            )
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class _UnixSocketHttpHandler(AbstractHTTPHandler):
    """
    urllib handler routing `unix://` requests through a Unix socket.
    """

    def __init__(self, socket_path: str, debuglevel: int | None = None):
        super().__init__(debuglevel)
        self._socket_path = socket_path

    # urllib wires schemes to handlers by method names. So unix:// urls will be
    # opened with this method
    def unix_open(self, req: Request) -> HTTPResponse:
        return self.do_open(
            _UnixSocketHttpConnection,
            req,
            socket_path=self._socket_path,
        )

    # Reuse the HTTP request preprocessing (Host, Content-Length, ...) for the
    # custom 'unix://' scheme.
    unix_request = AbstractHTTPHandler.do_request_


class _Transport(ABC):
    """
    Encapsulates how an APIv2 request is addressed and dispatched.
    """

    @property
    @abstractmethod
    def label(self) -> str:
        """
        Human readable endpoint identifier used in log messages.
        """

    @abstractmethod
    def _build_url(self, path: str) -> str:
        """
        Return valid URL for the given transport

        path -- path for the endpoint, beginning with '/'
        """

    @property
    @abstractmethod
    def _auth_headers(self) -> dict[str, str]:
        """
        Authentication headers to attach to every request
        """

    def build_request(self, path: str, json_body: bytes | None) -> Request:
        headers = (
            {"Content-Type": "application/json"}
            if json_body is not None
            else {}
        )
        return Request(
            self._build_url(path),
            data=json_body,
            headers={**headers, **self._auth_headers},
        )

    @abstractmethod
    def open(self, request: Request, timeout: int) -> HTTPResponse:
        """
        Send request and return the raw response context manager.
        """


class _HttpsTransport(_Transport):
    """
    Talk to a remote node over HTTPS using a token cookie for auth.
    """

    def __init__(self, addr: str, port: int, token: str, node_name: str):
        self._host = f"{_format_addr_for_url(addr)}:{port}"
        self._token = token
        self._node_name = node_name

    @property
    def label(self) -> str:
        return self._node_name

    def _build_url(self, path: str) -> str:
        return f"https://{self._host}{path}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Cookie": f"token={self._token}"}

    def open(self, request: Request, timeout: int) -> HTTPResponse:
        return urlopen(
            request, context=_UNVERIFIED_SSL_CONTEXT, timeout=timeout
        )


class _UnixSocketTransport(_Transport):
    """
    Talk to the local pcsd over its Unix domain socket.
    """

    def __init__(self, socket_path: str):
        self._socket_path = socket_path
        self._opener = build_opener(_UnixSocketHttpHandler(socket_path))

    @property
    def label(self) -> str:
        return f"localhost ({self._socket_path})"

    def _build_url(self, path: str) -> str:
        # The host is irrelevant for a Unix socket connection but urllib
        # requires a non-empty host; the connection ignores it and it only
        # ends up in the Host header. The actual endpoint is the socket path
        # carried by the handler.
        return f"unix://localhost{path}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        # No auth headers needed, the daemon authenticates the caller from the
        # socket peer credentials (SO_PEERCRED)
        return {}

    def open(self, request: Request, timeout: int) -> HTTPResponse:
        return self._opener.open(request, timeout=timeout)


class _ApiV2Client:
    """
    Low level client for APIv2
    """

    def __init__(self, logger: Logger):
        self._logger = logger

    def task_create(
        self, transport: _Transport, command: CommandDto, request_timeout: int
    ) -> str:
        """
        Create a task using APIv2 and return its identifier

        transport -- definition of the request target
        command -- definition of the command to execute
        request_timeout -- timeout for the requests
        """
        request = transport.build_request(
            "/api/v2/task/create",
            json_body=json.dumps(to_dict(command)).encode("utf-8"),
        )
        response = self._send_request(
            transport,
            request,
            request_error_on_400=True,
            request_timeout=request_timeout,
        )
        task_ident = self._parse_response(
            response, TaskIdentDto, transport.label
        ).task_ident
        self._logger.info("Created task %s on %s", task_ident, transport.label)
        return task_ident

    def task_result(
        self,
        transport: _Transport,
        task_ident: str,
        request_timeout: int,
        poll_interval: float = 1.0,
    ) -> Iterator[tuple[ReportItemList, bool, Any]]:
        """
        Poll for the result of the specified task on the specified request
        target. Return an iterator with new reports, task success, and any data
        returned by the task

        transport -- definition of the request target
        task_ident -- identification of the task
        request_timeout -- timeout for the requests
        poll_interval -- describes how often the polling should happen, in seconds
        """
        request = transport.build_request(
            f"/api/v2/task/result?task_ident={task_ident}", json_body=None
        )
        reports_seen = 0
        # TODO - dont do this if localhost. At the same time, what if the reports already have some context ????
        context = ReportItemContext(transport.label)

        # Poll indefinitely until node returns a finished state. No timeout
        # parameter — APIv2 supports long-running tasks. This hardcoded
        # behavior is intentional — no client needs anything else.
        while True:
            response = self._send_request(transport, request, request_timeout)
            result_dto = self._parse_response(
                response, TaskResultDto, transport.label
            )

            new_reports_dto = result_dto.reports[reports_seen:]
            reports_seen = len(result_dto.reports)

            self._log_poll_cycle(task_ident, result_dto, new_reports_dto)

            new_reports = [
                report_dto_to_item(r, context) for r in new_reports_dto
            ]
            if result_dto.task_finish_type == TaskFinishType.UNFINISHED:
                if new_reports:
                    yield new_reports, False, None
                time.sleep(poll_interval)
                continue

            yield (
                new_reports,
                result_dto.task_finish_type == TaskFinishType.SUCCESS,
                result_dto.result,
            )
            return

    def _parse_response(
        self, response: str, dto_class: type[_DTO], transport_label: str
    ) -> _DTO:
        try:
            return from_dict(dto_class, json.loads(response))
        except Exception as e:
            self._logger.error(
                "Response from %s does not conform to %s %s",
                transport_label,
                dto_class.__name__,
                e,
            )
            raise ApiV2CommunicationError(f"Unexpected response: {e}") from e

    def _send_request(
        self,
        transport: _Transport,
        request: Request,
        request_timeout: int,
        request_error_on_400: bool = False,
    ) -> str:
        try:
            self._logger.info("Sending request to %s", request.full_url)
            # Timeout applies to each individual HTTP request, not to the
            # overall operation. Both task/create and task/result are
            # lightweight server-side operations that should respond quickly.
            # The long-running part is the polling loop in task_result, which
            # is intentionally unbounded.
            with transport.open(request, request_timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as e:
            self._logger.info(
                "HTTP error: %s %s for %s", e.code, e.reason, request.full_url
            )
            # HTTP 400 from task/create means the payload is inherently
            # invalid — no node can process it. All 400 cases (api_v2.py):
            #   - malformed JSON body (prepare)
            #   - missing request body (RequestBodyMissingError)
            #   - missing required key in CommandDto (_from_dict_exc_handled)
            #   - unexpected keys in request body (_from_dict_exc_handled)
            #   - other payload structure violations (_from_dict_exc_handled)
            if request_error_on_400 and e.code == 400:
                raise ApiV2RequestError(
                    f"HTTP error {e.code}: {e.reason}"
                ) from e
            raise ApiV2CommunicationError(
                f"HTTP error {e.code}: {e.reason}"
            ) from e
        except URLError as e:
            self._logger.info(
                "URL error: %s for %s", e.reason, request.full_url
            )
            # Proxy env vars (https_proxy, all_proxy) are respected by
            # urllib and may cause connection failures when set
            # unintentionally. We intentionally do not disable proxy — it
            # can be legitimate (e.g. webUI managing a remote cluster).
            # Instead, we warn to aid debugging, consistent with the rest
            # of the codebase (NodeCommunicator, pcs/utils.py, pcsd/pcs.rb).
            # See: https://bugzilla.redhat.com/show_bug.cgi?id=1315627
            if is_proxy_set(os.environ):
                self._logger.warning(
                    "Proxy is set in environment variables, try disabling it"
                )
            raise ApiV2CommunicationError(f"URL error: {e.reason}") from e
        except Exception as e:
            self._logger.info(
                "Unexpected error %s for %s",
                e.__class__.__name__,
                request.full_url,
            )
            raise ApiV2CommunicationError(
                f"Unexpected error: {e.__class__.__name__}"
            ) from e

    def _log_poll_cycle(
        self,
        task_ident: str,
        result_dto: TaskResultDto,
        new_reports_dto: list,
    ) -> None:
        finish_type = result_dto.task_finish_type
        kill_reason = result_dto.kill_reason
        ident = task_ident
        reports = [to_dict(r) for r in new_reports_dto]

        if reports:
            self._logger.debug("Task %s new reports: %s", ident, reports)

        if finish_type == TaskFinishType.UNFINISHED:
            self._logger.info("Task %s status: %s", ident, finish_type)
            return

        self._logger.info("Task %s finished with status %s", ident, finish_type)

        if finish_type == TaskFinishType.SUCCESS:
            self._logger.info("Task %s result: %s", ident, result_dto.result)

        if finish_type == TaskFinishType.KILL and kill_reason:
            self._logger.info("Task %s kill reason: %s", ident, kill_reason)


class NodeApiV2Client:
    """
    APIv2 client for a specified target. Allows to run multiple commands on the
    same node.
    """

    CommunicationError = ApiV2CommunicationError
    RequestError = ApiV2RequestError

    def __init__(
        self,
        logger: Logger,
        transport: _Transport,
        effective_username: str | None = None,
        effective_groups: list[str] | None = None,
        request_timeout: int | None = None,
    ):
        self._logger = logger
        self._transport = transport
        self._effective_username = effective_username
        self._effective_groups = effective_groups
        self._request_timeout = (
            request_timeout
            if request_timeout is not None
            else settings.default_request_timeout
        )
        self._apiv2_client = _ApiV2Client(self._logger)

    @classmethod
    def for_unix_socket(
        cls,
        unix_socket_path: str,
        logger: Logger,
        effective_username: str | None = None,
        effective_groups: list[str] | None = None,
        request_timeout: int | None = None,
    ) -> "NodeApiV2Client":
        return cls(
            logger,
            _UnixSocketTransport(unix_socket_path),
            effective_username,
            effective_groups,
            request_timeout,
        )

    def run_command(
        self,
        command_name: str,
        command_params: Mapping[str, Any],
        poll_interval: float = 1.0,
    ) -> Iterator[tuple[ReportItemList, bool, Any]]:
        """
        Run an APIv2 command, and return an iterator with new reports, task
        success, and any data returned by the task

        command_name -- name of the APIv2 command to execute
        command_params -- params of the command
        poll_interval -- interval in seconds, for polling of the command result
        """

        task_ident = self._create_task(command_name, command_params)
        return self._get_task_result(task_ident, poll_interval)

    def _create_task(
        self, command_name: str, command_params: Mapping[str, Any]
    ) -> str:
        return self._apiv2_client.task_create(
            self._transport,
            CommandDto(
                command_name=command_name,
                params=dict(command_params),
                options=CommandOptionsDto(
                    request_timeout=self._request_timeout,
                    effective_username=self._effective_username,
                    effective_groups=self._effective_groups,
                ),
            ),
            self._request_timeout,
        )

    def _get_task_result(
        self, task_ident: str, poll_interval: float = 1.0
    ) -> Iterator[tuple[ReportItemList, bool, Any]]:
        return self._apiv2_client.task_result(
            self._transport, task_ident, self._request_timeout, poll_interval
        )
