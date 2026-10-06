from typing import TYPE_CHECKING, Any

from pcs.cli.reports.output import error, process_library_reports
from pcs.common.communication.api_v2_client import ApiV2Connector
from pcs.lib.errors import LibraryError

if TYPE_CHECKING:
    from pcs.common import reports


# TODO: type stubs generated from COMMAND_MAPPING
class ApiV2CommandProxy:
    def __init__(self, name: str, api_client: ApiV2Connector):
        self._name = name
        self._api_client = api_client

    def __call__(self, **kwargs: Any) -> Any:
        return _run_command(self._api_client, self._name, kwargs)

    def __getattr__(self, name: str) -> "ApiV2CommandProxy":
        return ApiV2CommandProxy(
            f"{self._name}.{name}" if self._name else name, self._api_client
        )


def _run_command(
    api_v2_client: ApiV2Connector,
    command_name: str,
    command_params: dict[str, Any],
) -> Any:
    success = False
    new_reports: reports.ReportItemList = []
    command_result = None
    try:
        for result in api_v2_client.run_command(command_name, command_params):
            new_reports, success, command_result = result
            if new_reports:
                process_library_reports(new_reports, exit_on_error=False)
    except (api_v2_client.RequestError, api_v2_client.CommunicationError) as e:
        # TODO: better errors
        # TODO: we might need more exception types
        error(str(e))
        raise LibraryError() from e

    if success:
        return command_result

    # Command was not successful, or some exception happened
    # TODO: this means we have the same handling in pcs/app.py for these commands
    # and for commands that we import and run directly.
    # We also externally behave the same as when we imported the commands directly
    raise LibraryError()
