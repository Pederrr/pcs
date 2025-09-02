from typing import Sequence, cast

from pcs.common import reports
from pcs.common.file import RawFileError
from pcs.common.file_type_codes import PCS_KNOWN_HOSTS
from pcs.common.host import PcsKnownHost
from pcs.common.node_communicator import Communicator, RequestTarget
from pcs.lib.communication.pcs_cfgsync import SetConfigs, SetConfigsResult
from pcs.lib.communication.tools import run
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.interface.config import (
    ParserErrorException,
    SyncVersionFacadeInterface,
)
from pcs.lib.pcs_cfgsync.fetcher import ConfigFetcher


def save_sync_new_version(
    file_instance: FileInstance,
    file: SyncVersionFacadeInterface,
    cluster_name: str,
    target_list: Sequence[RequestTarget],
    node_communicator: Communicator,
    report_processor: reports.ReportProcessor,
    fetch_on_conflict: bool,
) -> bool:
    file.set_data_version(file.data_version + 1)
    com_cmd = SetConfigs(
        report_processor,
        cluster_name,
        {
            file_instance.toolbox.file_type_code: file_instance.facade_to_raw(
                file
            ).decode("utf-8")
        },
    )
    com_cmd.set_targets(target_list)  # type: ignore[no-untyped-call]
    results = run(node_communicator, com_cmd)  # type: ignore[no-untyped-call]

    if all(
        results[node].get(PCS_KNOWN_HOSTS) == SetConfigsResult.ACCEPTED
        for node in results
    ):
        # we were able to successfully save the files on all cluster nodes
        return True

    if fetch_on_conflict:
        fetcher = ConfigFetcher(node_communicator, report_processor)
        newest_files, _ = fetcher.fetch(cluster_name, target_list)

        try:
            if file_instance.toolbox.file_type_code in newest_files:
                file_instance.write_facade(
                    newest_files[file_instance.toolbox.file_type_code],
                    can_overwrite=True,
                )
        except RawFileError as e:
            report_processor.report(raw_file_error_report(e))

    # TODO report about conflict,
    return False


def save_sync_new_known_hosts(
    new_hosts: Sequence[PcsKnownHost],
    cluster_name: str,
    target_list: Sequence[RequestTarget],
    node_communicator: Communicator,
    report_processor: reports.ReportProcessor,
) -> bool:
    known_hosts_instance = FileInstance.for_known_hosts()
    known_hosts_exists = known_hosts_instance.raw_file.exists()
    try:
        if not known_hosts_exists:
            known_hosts_facade = KnownHostsFacade.create()
        else:
            known_hosts_facade = cast(
                KnownHostsFacade, known_hosts_instance.read_to_facade()
            )
    except RawFileError as e:
        report_processor.report(raw_file_error_report(e))
        return False
    except ParserErrorException as e:
        report_processor.report_list(
            known_hosts_instance.parser_exception_to_report_list(e)
        )
        return False

    old_local_known_hosts = known_hosts_facade.config
    known_hosts_facade.update_known_hosts(new_hosts)
    if known_hosts_exists:
        known_hosts_facade.set_data_version(known_hosts_facade.data_version + 1)

    com_cmd = SetConfigs(
        report_processor,
        cluster_name,
        {
            PCS_KNOWN_HOSTS: known_hosts_instance.facade_to_raw(
                known_hosts_facade
            ).decode("utf-8")
        },
    )
    com_cmd.set_targets(target_list)  # type: ignore[no-untyped-call]
    results = run(node_communicator, com_cmd)  # type: ignore[no-untyped-call]

    if all(
        results[node].get(PCS_KNOWN_HOSTS) == SetConfigsResult.ACCEPTED
        for node in results
    ):
        # we were able to successfully save the files on all cluster nodes
        return True

    # some nodes had newer configs
    # find the newest config from the cluster
    fetcher = ConfigFetcher(node_communicator, report_processor)
    files, _ = fetcher.fetch(cluster_name, target_list)
    # TODO the config might not be there, in that case produces some error
    newest_known_hosts_from_cluster = cast(
        KnownHostsFacade, files[PCS_KNOWN_HOSTS]
    )

    known_hosts_facade = KnownHostsFacade(old_local_known_hosts)
    known_hosts_facade.set_data_version(
        newest_known_hosts_from_cluster.data_version
    )
    # first add the tokens from the cluster then the new tokens, so we overwrite
    # any tokens that were previously in the configs with the new tokens
    known_hosts_facade.update_known_hosts(
        list(newest_known_hosts_from_cluster.known_hosts.values())
    )
    known_hosts_facade.update_known_hosts(new_hosts)

    return save_sync_new_version(
        known_hosts_instance,
        known_hosts_facade,
        cluster_name,
        target_list,
        node_communicator,
        report_processor,
        fetch_on_conflict=True,
    )
