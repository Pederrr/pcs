from pcs.common import reports
from pcs.common.file import RawFileError
from pcs.common.pcs_cfgsync_dto import SyncConfigsDto
from pcs.lib.communication.pcs_cfgsync import SetConfigs, SetConfigsResult
from pcs.lib.communication.tools import run_and_raise
from pcs.lib.env import LibraryEnvironment, LibraryError
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.node import get_existing_nodes_names
from pcs.lib.pcs_cfgsync.const import SYNCED_CONFIGS


def get_configs(env: LibraryEnvironment, cluster_name: str) -> SyncConfigsDto:
    """
    Get contents of synced configuration files from node

    cluster_name -- expected cluster name. End with an error if the requested
        node is not in the cluster with the expected name.
    """
    current_cluster_name = env.get_corosync_conf().get_cluster_name()
    if current_cluster_name != cluster_name:
        env.report_processor.report(
            reports.ReportItem.error(
                reports.messages.NodeReportsUnexpectedClusterName(cluster_name)
            )
        )
    if env.report_processor.has_errors:
        raise LibraryError()

    configs = {}
    for file_type_code in SYNCED_CONFIGS:
        file_instance = FileInstance.for_common(file_type_code)
        if not file_instance.raw_file.exists():
            # it's not an error if the file does not exist locally, we just
            # wont send it back
            continue
        try:
            configs[file_type_code] = file_instance.read_raw().decode("utf-8")
        except RawFileError as e:
            # in case of error when reading some file, we still might be able
            # to read and send the others without issues
            env.report_processor.report(
                raw_file_error_report(e, is_forced_or_warning=True)
            )

    return SyncConfigsDto(current_cluster_name, configs)


def send_local_configs_to_cluster_nodes(
    env: LibraryEnvironment, force_flags: reports.types.ForceFlags = ()
) -> None:
    """
    Send local configs to all cluster nodes
    """
    corosync_conf = env.get_corosync_conf()
    cluster_name = corosync_conf.get_cluster_name()

    local_configs = {}
    # TODO code repetition with the function up there
    for filetype_code in SYNCED_CONFIGS:
        file_instance = FileInstance.for_common(filetype_code)
        if not file_instance.raw_file.exists():
            continue
        try:
            local_configs[filetype_code] = file_instance.read_raw().decode(
                "utf-8"
            )
        except RawFileError as e:
            env.report_processor.report(
                raw_file_error_report(e, is_forced_or_warning=True)
            )

    # There are no configs to be sent, we can end
    if not local_configs:
        return

    node_names, report_list = get_existing_nodes_names(corosync_conf, None)
    target_list = env.get_node_target_factory().get_target_list(node_names)  # type: ignore [no-untyped-call]
    if env.report_processor.report_list(report_list).has_errors:
        raise LibraryError()

    node_communicator = env.get_node_communicator()
    com_cmd = SetConfigs(
        env.report_processor,
        cluster_name,
        local_configs,
        skip_offline_targets=reports.codes.SKIP_OFFLINE_NODES in force_flags,
        force=reports.codes.FORCE in force_flags,
    )
    com_cmd.set_targets(target_list)  # type: ignore [no-untyped-call]
    result = run_and_raise(node_communicator, com_cmd)  # type: ignore [no-untyped-call]

    # TODO check the result and report stuff
    # if some status is rejected, then report
    for node_name in result:
        for cfg_filetype_code, cfg_result in result[node_name].items():
            if cfg_result == SetConfigsResult.REJECTED:
                # TODO some report
                print(cfg_filetype_code)
