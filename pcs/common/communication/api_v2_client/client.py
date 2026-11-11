import json
import logging
import os
import time
from collections.abc import Iterator, Mapping
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request

from pcs import settings
from pcs.common.async_tasks.dto import (
    CommandDto,
    CommandOptionsDto,
    TaskIdentDto,
    TaskResultDto,
)
from pcs.common.async_tasks.types import TaskFinishType
from pcs.common.communication.utils import is_proxy_set
from pcs.common.interface.dto import DataTransferObject, from_dict, to_dict
from pcs.common.reports import (
    ReportItemContext,
    ReportItemList,
    report_dto_to_item,
)

from .errors import (
    ApiV2CommunicationError,
    ApiV2ConnectionError,
    ApiV2ProtocolError,
    ApiV2RequestError,
    ApiV2TimeoutError,
)
from .transport import Transport, UnixSocketTransport

_DTO = TypeVar("_DTO", bound=DataTransferObject)


def _request_data(request: Request) -> str:
    if request.data is None:
        return ""
    if isinstance(request.data, bytes):
        return request.data.decode("utf-8")
    return str(request.data)


class _ApiV2Client:
    def __init__(
        self,
        transport: Transport,
        logger: logging.Logger,
        context_node_name: str | None = None,
        api_v2_timeout: float = settings.default_request_timeout,
    ):
        self._logger = logger
        self._transport = transport
        self._api_v2_timeout = api_v2_timeout
        self._report_item_context = (
            None
            if context_node_name is None
            else ReportItemContext(context_node_name)
        )

    def task_create(self, command: CommandDto) -> str:
        """
        Create a task using APIv2 and return its identifier

        command -- definition of the command to execute
        """
        request = self._transport.build_request(
            "/api/v2/task/create",
            json_body=json.dumps(to_dict(command)).encode("utf-8"),
        )
        response = self._send_request(request, request_error_on_400=True)
        task_ident = self._parse_response(response, TaskIdentDto).task_ident
        self._logger.debug(
            "Created task '%s' on %s", task_ident, self._transport.label
        )
        return task_ident

    def task_result(
        self, task_ident: str, poll_interval: float = 1.0
    ) -> Iterator[tuple[ReportItemList, bool, Any]]:
        """
        Poll for the result of the specified task. Return an iterator with new
        reports, task success, and any data returned by the task

        task_ident -- identification of the task
        poll_interval -- describes how often the polling should happen, in seconds
        """
        request = self._transport.build_request(
            "/api/v2/task/result", query_params={"task_ident": task_ident}
        )
        reports_seen = 0

        # Poll indefinitely until node returns a finished state. No timeout
        # parameter — APIv2 supports long-running tasks. This hardcoded
        # behavior is intentional — no client needs anything else.
        while True:
            response = self._send_request(request)
            result_dto = self._parse_response(response, TaskResultDto)

            new_reports_dto = result_dto.reports[reports_seen:]
            reports_seen = len(result_dto.reports)

            self._report_poll_cycle(task_ident, result_dto)

            new_reports = [
                report_dto_to_item(r, self._report_item_context)
                for r in new_reports_dto
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

    def _parse_response(self, response: str, dto_class: type[_DTO]) -> _DTO:
        try:
            return from_dict(dto_class, json.loads(response))
        except Exception as e:
            self._logger.debug(
                "Response from %s does not conform to %s: %s",
                self._transport.label,
                dto_class.__name__,
                str(e),
            )
            raise ApiV2ProtocolError(
                self._transport.label, detail=str(e)
            ) from e

    def _send_request(
        self, request: Request, request_error_on_400: bool = False
    ) -> str:
        try:
            self._log_request_started(request)
            # Timeout applies to each individual HTTP request, not to the
            # overall operation. Both task/create and task/result are
            # lightweight server-side operations that should respond quickly.
            # The long-running part is the polling loop in task_result, which
            # is intentionally unbounded.
            with self._transport.open(
                request, self._api_v2_timeout
            ) as response:
                body = response.read().decode("utf-8")
                self._logger.debug(
                    "Finished calling: %s\nResponse code: %s\n"
                    "--Debug Response Start--\n%s\n--Debug Response End--",
                    request.full_url,
                    response.status,
                    body,
                )
                return body
        except HTTPError as e:
            # Though being an exception (a subclass of URLError), an HTTPError
            # can also function as a non-exceptional file-like return value
            # (the same thing that urlopen() returns).
            # (https://docs.python.org/3/library/urllib.error.html)
            #
            # We should close the file after working with it, otherwise we
            # can get `ResourceWarning` about not closing the "file".
            with e:
                self._logger.debug(
                    "Error calling %s: HTTP error: %s %s",
                    request.full_url,
                    e.code,
                    e.reason,
                )
                # HTTP 400 from task/create means the payload is inherently
                # invalid — no node can process it. All 400 cases (api_v2.py):
                #   - malformed JSON body (prepare)
                #   - missing request body (RequestBodyMissingError)
                #   - missing required key in CommandDto (_from_dict_exc_handled)
                #   - unexpected keys in request body (_from_dict_exc_handled)
                #   - other payload structure violations (_from_dict_exc_handled)
                reason = str(e.reason)
                endpoint = self._transport.label
                if request_error_on_400 and e.code == 400:
                    raise ApiV2RequestError(endpoint, e.code, reason) from e
                raise ApiV2CommunicationError(endpoint, e.code, reason) from e
        except URLError as e:
            # Proxy env vars (https_proxy, all_proxy) are respected by
            # urllib and may cause connection failures when set
            # unintentionally. We intentionally do not disable proxy — it
            # can be legitimate.
            # Instead, we warn to aid debugging, consistent with the rest
            # of the codebase (NodeCommunicator, pcs/utils.py, pcsd/pcs.rb).
            # See: https://bugzilla.redhat.com/show_bug.cgi?id=1315627
            # Only warn when the transport actually goes through a proxy.
            if self._transport.respects_proxy and is_proxy_set(os.environ):
                self._logger.warning(
                    "Proxy is set in environment variables, try disabling it"
                )

            endpoint = self._transport.label
            # socket.timeout is an alias of TimeoutError since Python 3.10
            if isinstance(e.reason, TimeoutError):
                self._logger.debug(
                    "Calling %s timed out: %s", request.full_url, str(e.reason)
                )
                raise ApiV2TimeoutError(endpoint, self._api_v2_timeout) from e

            # URLError.reason wraps the underlying cause of the failure. When that
            # cause is an OSError, the request never got a usable response because
            # we could not establish the connection in the first place, so we treat
            # it as a connection error. This covers all the low-level failures we
            # care about, because they all subclass OSError:
            # - ConnectionError (e.g. connection refused/reset) is an OSError
            # - DNS resolution failures (socket.gaierror) are OSErrors
            # - SSL failures - ssl.SSLError
            # - failures to open the unix socket file are OSErrors
            # Anything else is a reason we don't specifically recognize, so we fall
            # back to a generic communication error.
            if isinstance(e.reason, OSError):
                self._logger.debug(
                    "Unable to connect to %s when calling %s: %s",
                    self._transport.label,
                    request.full_url,
                    str(e.reason),
                )
                raise ApiV2ConnectionError(
                    endpoint, reason=str(e.reason)
                ) from e
            self._logger.debug(
                "Error calling %s: %s", request.full_url, str(e.reason)
            )
            raise ApiV2CommunicationError(endpoint, reason=str(e.reason)) from e
        except TimeoutError as e:
            # A socket timeout may surface directly instead of wrapped in a
            # URLError
            self._logger.debug(
                "Calling %s timed out: %s", request.full_url, str(e)
            )
            raise ApiV2TimeoutError(
                self._transport.label, self._api_v2_timeout
            ) from e
        except Exception as e:
            self._logger.debug(
                "Unexpected error when calling %s: %s",
                request.full_url,
                e.__class__.__name__,
            )
            raise ApiV2CommunicationError(
                self._transport.label,
                reason=f"Unexpected error: {e.__class__.__name__}",
            ) from e

    def _log_request_started(self, request: Request) -> None:
        data = _request_data(request)
        if data:
            self._logger.debug(
                "Sending HTTP request to: %s\n"
                "--Debug Input Start--\n%s\n--Debug Input End--",
                request.full_url,
                data,
            )
        else:
            self._logger.debug("Sending HTTP request to: %s", request.full_url)

    def _report_poll_cycle(
        self, task_ident: str, result_dto: TaskResultDto
    ) -> None:
        finish_type = result_dto.task_finish_type
        detail = ""
        if finish_type == TaskFinishType.SUCCESS:
            detail = str(result_dto.result)
        elif finish_type == TaskFinishType.KILL and result_dto.kill_reason:
            detail = f"kill reason: {result_dto.kill_reason}"
        detail_suffix = f" ({detail})" if detail else ""
        self._logger.debug(
            "Task '%s' status: %s%s", task_ident, finish_type, detail_suffix
        )


class ApiV2Connector:
    """
    APIv2 client for a specified target. Allows to run multiple commands on the
    same node under the same user.
    """

    def __init__(
        self,
        logger: logging.Logger,
        transport: Transport,
        context_node_name: str | None = None,
        effective_username: str | None = None,
        effective_groups: list[str] | None = None,
        request_timeout: int | None = None,
        api_v2_timeout: float = settings.default_request_timeout,
    ):
        self._request_timeout = request_timeout
        self._effective_username = effective_username
        self._effective_groups = effective_groups
        self._apiv2_client = _ApiV2Client(
            transport, logger, context_node_name, api_v2_timeout
        )

    @classmethod
    def for_unix_socket(
        cls,
        unix_socket_path: str,
        logger: logging.Logger,
        effective_username: str | None = None,
        effective_groups: list[str] | None = None,
        request_timeout: int | None = None,
        api_v2_timeout: float = settings.default_request_timeout,
    ) -> "ApiV2Connector":
        """
        Create connector for local unix socket

        unix_socket_path -- file path of the unix socket
        logger -- logger for communication diagnostics
        effective_username -- name of effective user for the APIv2 tasks
        effective_groups -- groups of effective user for the APIv2 tasks
        request_timeout -- timeout for the requests done inside created APIv2
            tasks
        api_v2_timeout -- timeout for requests done to APIv2 endpoints
        """
        return cls(
            logger,
            UnixSocketTransport(unix_socket_path),
            None,
            effective_username,
            effective_groups,
            request_timeout,
            api_v2_timeout,
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

        task_ident = self._apiv2_client.task_create(
            CommandDto(
                command_name=command_name,
                params=dict(command_params),
                options=CommandOptionsDto(
                    request_timeout=self._request_timeout,
                    effective_username=self._effective_username,
                    effective_groups=self._effective_groups,
                ),
            )
        )
        return self._apiv2_client.task_result(task_ident, poll_interval)
