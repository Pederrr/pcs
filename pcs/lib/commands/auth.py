from typing import Any, Mapping, cast

from pcs.common import reports
from pcs.common.file import RawFileError
from pcs.common.node_communicator import PcsKnownHost
from pcs.lib import validate
from pcs.lib.env import LibraryEnvironment, LibraryError
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.interface.config import ParserErrorException


# TODO update this whole mess
def _validate_hosts_with_token(
    hosts_dict: Mapping[str, Any],
) -> reports.ReportItemList:
    report_list = []
    # TODO update the TODOs
    for host_name in hosts_dict:
        report_list.extend(
            validate.ValueNotEmptyNotNone("host name", "").validate(
                {"host name": host_name}
            )
        )
        report_list.extend(
            # - TODO - should we also check the upper bound of the token length ?
            #    - TODO - the tokens from --token are base64 encoded
            validate.ValidatorAll(
                [
                    validate.IsRequiredAll(["dest_list", "token"], "host"),
                    validate.ValueNotEmptyNotNone(
                        "token", f"non-empty string for node '{host_name}'"
                    ),
                ]
            ).validate(hosts_dict[host_name])
        )
        dest_list = hosts_dict[host_name].get("dest_list", [])
        if not dest_list:
            report_list.append(
                reports.ReportItem.error(
                    reports.messages.InvalidOptionValue(
                        "dest_list",
                        dest_list,
                        "non-empty list of destinations",
                        cannot_be_empty=True,
                    )
                )
            )
        for dest in dest_list:
            report_list.extend(
                validate.ValidatorAll(
                    [
                        validate.IsRequiredAll(
                            ["addr", "port"], option_type="dest_list"
                        ),
                        validate.ValueNotEmptyNotNone(
                            "addr", f"address for node '{host_name}'"
                        ),
                        validate.ValuePortNumber(
                            "port", f"port for node '{host_name}'"
                        ),
                    ]
                ).validate(dest)
            )
    return report_list


def auth_hosts_token_no_sync(
    env: LibraryEnvironment,
    hosts_dict: Mapping[str, Any],
) -> None:
    """
    TODO
    """
    if env.report_processor.report_list(
        _validate_hosts_with_token(hosts_dict)
    ).has_errors:
        raise LibraryError()

    hosts = [
        PcsKnownHost.from_known_host_file_dict(name, value)
        for name, value in hosts_dict.items()
    ]

    file_instance = FileInstance.for_known_hosts()
    known_hosts_exists = file_instance.raw_file.exists()
    try:
        if not known_hosts_exists:
            known_hosts_facade = KnownHostsFacade.create()
        else:
            known_hosts_facade = cast(
                KnownHostsFacade, file_instance.read_to_facade()
            )
    except RawFileError as e:
        env.report_processor.report(raw_file_error_report(e))
    except ParserErrorException as e:
        env.report_processor.report_list(
            file_instance.parser_exception_to_report_list(e)
        )
    if env.report_processor.has_errors:
        raise LibraryError()

    known_hosts_facade.update_known_hosts(hosts)
    if known_hosts_exists:
        known_hosts_facade.set_data_version(known_hosts_facade.data_version + 1)

    try:
        file_instance.write_facade(known_hosts_facade, can_overwrite=True)
    except RawFileError as e:
        env.report_processor.report(raw_file_error_report(e))
    if env.report_processor.has_errors:
        raise LibraryError()
