import json
import os
import time
from collections.abc import Iterator, Mapping
from logging import Logger
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
from pcs.common.interface.dto import DataTransferObject, from_dict, to_dict
from pcs.common.reports import (
    ReportItemContext,
    ReportItemDto,
    ReportItemList,
    report_dto_to_item,
)
from pcs.lib.external import is_proxy_set

from .transport import Transport, UnixSocketTransport

_DTO = TypeVar("_DTO", bound=DataTransferObject)


class ApiV2CommunicationError(Exception):
    pass


class ApiV2RequestError(Exception):
    """The request payload is invalid; retrying on another node won't help."""


class _ApiV2Client:
    def __init__(
        self,
        transport: Transport,
        logger: Logger,
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
        task_ident = self._parse_response(
            response, TaskIdentDto, self._transport.host_label
        ).task_ident
        self._logger.info(
            "Created task %s on %s", task_ident, self._transport.host_label
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
            f"/api/v2/task/result?task_ident={task_ident}", json_body=None
        )
        reports_seen = 0

        # Poll indefinitely until node returns a finished state. No timeout
        # parameter — APIv2 supports long-running tasks. This hardcoded
        # behavior is intentional — no client needs anything else.
        while True:
            response = self._send_request(request)
            result_dto = self._parse_response(
                response, TaskResultDto, self._transport.host_label
            )

            new_reports_dto = result_dto.reports[reports_seen:]
            reports_seen = len(result_dto.reports)

            self._log_poll_cycle(task_ident, result_dto, new_reports_dto)

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
        self, request: Request, request_error_on_400: bool = False
    ) -> str:
        try:
            self._logger.info("Sending request to %s", request.full_url)
            # Timeout applies to each individual HTTP request, not to the
            # overall operation. Both task/create and task/result are
            # lightweight server-side operations that should respond quickly.
            # The long-running part is the polling loop in task_result, which
            # is intentionally unbounded.
            with self._transport.open(
                request, self._api_v2_timeout
            ) as response:
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
        new_reports_dto: list[ReportItemDto],
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


class ApiV2Connector:
    """
    APIv2 client for a specified target. Allows to run multiple commands on the
    same node under the same user.
    """

    CommunicationError = ApiV2CommunicationError
    RequestError = ApiV2RequestError

    def __init__(
        self,
        logger: Logger,
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
        logger: Logger,
        effective_username: str | None = None,
        effective_groups: list[str] | None = None,
        request_timeout: int | None = None,
        api_v2_timeout: float = settings.default_request_timeout,
    ) -> "ApiV2Connector":
        """
        Create connector for local unix socket

        unix_socket_path -- file path of the unix socket
        logger -- instance for logging
        effective_username --
        effective_groups --
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
