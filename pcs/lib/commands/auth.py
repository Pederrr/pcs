from typing import Sequence, cast

from pcs.common.file import RawFileError
from pcs.common.node_communicator import PcsKnownHost
from pcs.lib.env import LibraryEnvironment, LibraryError
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.interface.config import ParserErrorException


def auth_hosts_token_no_sync(
    env: LibraryEnvironment,
    hosts: Sequence[PcsKnownHost],
) -> None:
    """
    TODO
    """
    print(hosts)
    for host in hosts:
        # check non empty string in name
        # - TODO - should we also check the upper bound of the token length ?
        #    - TODO - the tokens from --token are base64 encoded
        if len(host.token) == 0:
            # TODO report error
            pass
        # validate addressess in dest_list
    if env.report_processor.has_errors:
        raise LibraryError()

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
