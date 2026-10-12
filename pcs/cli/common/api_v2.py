from typing import Any

from pcs.common import reports
from pcs.common.communication.api_v2_client import (
    ApiV2ClientError,
    ApiV2ConnectionError,
    ApiV2Connector,
    ApiV2TimeoutError,
)
from pcs.lib.errors import LibraryError


# The static type of this dynamic proxy is described by the generated stub
# api_v2.pyi (see scripts/gen_apiv2_stubs.py, `make gen-apiv2-stubs`), which maps
# COMMAND_MAP onto typed nested namespaces.
class ApiV2CommandProxy:
    """
    TODO
    """

    def __init__(
        self,
        api_client: ApiV2Connector,
        report_processor: reports.ReportProcessor,
        command_name: str = "",
    ):
        self._command_name = command_name
        self._api_client = api_client
        self._report_processor = report_processor

    def __getattr__(self, name: str) -> "ApiV2CommandProxy":
        return ApiV2CommandProxy(
            self._api_client,
            self._report_processor,
            f"{self._command_name}.{name}" if self._command_name else name,
        )

    def __call__(self, **kwargs: Any) -> Any:
        success = False
        new_reports: reports.ReportItemList = []
        command_result = None
        try:
            for result in self._api_client.run_command(
                self._command_name, dict(kwargs)
            ):
                new_reports, success, command_result = result
                self._report_processor.report_list(new_reports)
        except ApiV2ConnectionError as e:
            # original error message when pcsd was not running:
            # unable to connect to localhost, check if pcsd is running or try
            # setting higher timeout with --request-timeout option (<reason>)
            """
            error(
                f"Unable to connect to pcsd: {str(e)}\n"
                "Check if pcsd is running and your user is in the "
                f"{settings.pacemaker_gname} group"
            )
            """
            # TODO what if the user is root?
            # then we write to make sure he is in haclient even tho they dont have to be

            # self._report_processor.report(TODO)
            raise LibraryError() from e
        except ApiV2TimeoutError as e:
            """
            error(
                f"Unable to connect to pcsd: {str(e)}\n"
                "Try setting higher timeout with '--request-timeout' option"
            )
            """
            # self._report_processor.report(TODO)
            raise LibraryError() from e
        except ApiV2ClientError as e:
            """
            error(f"Unable to communicate with pcsd: {str(e)}")
            """
            # self._report_processor.report(TODO)
            raise LibraryError() from e

        if success:
            return command_result

        # Command was not successful
        # Raising LibraryError so that calling a lib command through APIv2 behaves
        # similarly to directly importing and using the lib command - we can use the
        # same error handling in pcs/app.py for both
        raise LibraryError()
