from dataclasses import asdict
from typing import Mapping, Sequence, cast

from pcs.common import reports
from pcs.common.file import RawFileError
from pcs.common.host import Destination
from pcs.common.node_communicator import PcsKnownHost, RequestTarget
from pcs.lib import validate
from pcs.lib.communication.nodes import Auth
from pcs.lib.communication.tools import run
from pcs.lib.env import LibraryEnvironment, LibraryError
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.interface.config import ParserErrorException
from pcs.lib.node import get_existing_nodes_names
from pcs.lib.pcs_cfgsync.save_sync import save_sync_new_known_hosts


def _validate_destinations(
    host_name: str, destinations: Sequence[Destination]
) -> reports.ReportItemList:
    report_list = []
    if not destinations:
        report_list.append(
            reports.ReportItem.error(
                reports.messages.InvalidOptionValue(
                    "dest_list",
                    str(destinations),
                    f"non-empty list of destinations for node '{host_name}'",
                    cannot_be_empty=True,
                )
            )
        )
    for dest in destinations:
        report_list.extend(
            validate.ValidatorAll(
                [
                    validate.ValueNotEmpty(
                        "addr", f"address for node '{host_name}'"
                    ),
                    validate.ValuePortNumber(
                        "port", f"port for node '{host_name}'"
                    ),
                ]
            ).validate(asdict(dest))
        )

    return report_list


def _validate_hosts_with_token(
    hosts: Sequence[PcsKnownHost],
) -> reports.ReportItemList:
    report_list = []
    if not hosts:
        report_list.append(
            reports.ReportItem.error(reports.messages.NoHostSpecified())
        )
    for host in hosts:
        report_list.extend(
            # - TODO - should we also check the upper bound of the token length ?
            #    - TODO - the tokens from --token are base64 encoded
            validate.ValidatorAll(
                [
                    validate.ValueNotEmpty("name", "node name"),
                    validate.ValueNotEmpty(
                        "token", f"non-empty string for node '{host.name}'"
                    ),
                ]
            ).validate(asdict(host))
        )
        report_list.extend(_validate_destinations(host.name, host.dest_list))

    return report_list


def _validate_hosts(
    hosts: Mapping[str, Sequence[Destination]],
) -> reports.ReportItemList:
    report_list = []
    if not hosts:
        report_list.append(
            reports.ReportItem.error(reports.messages.NoHostSpecified())
        )
    for host_name in hosts:
        report_list.extend(
            validate.ValueNotEmpty("host name", "").validate(
                {"host name": host_name}
            )
        )

        report_list.extend(_validate_destinations(host_name, hosts[host_name]))

    return report_list


# we can use dataclasses as arguments, apiv2 supports it:
# - daemon.async_tasks.worker.executor -> the typehints from the command
# are used to translate and validate the arguments
def auth_hosts_token_no_sync(
    env: LibraryEnvironment, hosts: Sequence[PcsKnownHost]
) -> None:
    """
    TODO
    """
    if env.report_processor.report_list(
        _validate_hosts_with_token(hosts)
    ).has_errors:
        raise LibraryError()
    # TODO check that there are no duplicities in host names

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


def auth_hosts(
    env: LibraryEnvironment,
    username: str,
    password: str,
    hosts: Mapping[str, Sequence[Destination]],
) -> None:
    """
    TODO
    """
    if env.report_processor.report_list(_validate_hosts(hosts)).has_errors:
        raise LibraryError()

    request_targets = [
        RequestTarget(label=host_name, dest_list=list(destinations))
        for host_name, destinations in hosts.items()
    ]

    node_communicator = env.get_node_communicator()
    com_cmd = Auth(username, password, env.report_processor)
    com_cmd.set_targets(request_targets)
    # we do not want to raise LibraryError in case only some nodes returned
    # errors, since we want to update the known-hosts file with whatever tokens
    # we were able to receive - this is how the old impl behaved
    # this means we cannot blindly check errors by using processor.has_errors
    received_tokens: dict[str, str] = run(node_communicator, com_cmd)

    new_known_hosts = [
        PcsKnownHost(
            name=target.label,
            token=received_tokens[target.label],
            dest_list=target.dest_list,
        )
        for target in request_targets
        if target.label in received_tokens
    ]

    if not new_known_hosts:
        if env.report_processor.has_errors:
            raise LibraryError()
        return

    if FileInstance.for_corosync_conf().raw_file.exists():
        # we are in cluster, so we distribute the new tokens
        corosync_conf = env.get_corosync_conf()
        cluster_name = corosync_conf.get_cluster_name()

        # we want to send the tokens to all cluster nodes, but we want to use
        # the new tokens in case we ran auth on any nodes that are already in
        # the cluster
        node_names, report_list = get_existing_nodes_names(corosync_conf, None)
        env.report_processor.report_list(report_list)
        new_hosts_already_in_cluster = set(received_tokens) & set(node_names)
        target_list = env.get_node_target_factory().get_target_list(
            set(node_names) - new_hosts_already_in_cluster
        )  # type: ignore [no-untyped-call]
        target_list.extend(
            RequestTarget.from_known_host(host)
            for host in new_known_hosts
            if host.name in new_hosts_already_in_cluster
        )

        no_conflict = save_sync_new_known_hosts(
            new_known_hosts,
            cluster_name,
            target_list,
            node_communicator,
            env.report_processor,
        )
        if not no_conflict:
            # TODO report ?
            raise LibraryError()
        return

    # we are not running in a cluster, so just save the new tokens locally
    # TODO copy-pasted code from command above
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
        raise LibraryError() from e
    except ParserErrorException as e:
        env.report_processor.report_list(
            file_instance.parser_exception_to_report_list(e)
        )
        raise LibraryError() from e

    known_hosts_facade.update_known_hosts(new_known_hosts)
    if known_hosts_exists:
        known_hosts_facade.set_data_version(known_hosts_facade.data_version + 1)
    try:
        file_instance.write_facade(known_hosts_facade, can_overwrite=True)
    except RawFileError as e:
        env.report_processor.report(raw_file_error_report(e))
        raise LibraryError() from e
