from typing import Mapping, cast

from pcs.common.auth import HostAuthData, HostWithTokenAuthData
from pcs.common.file import RawFileError
from pcs.common.node_communicator import PcsKnownHost, RequestTarget
from pcs.lib.auth import validations
from pcs.lib.communication.nodes import Auth
from pcs.lib.communication.tools import run
from pcs.lib.env import LibraryEnvironment, LibraryError
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import raw_file_error_report
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.interface.config import ParserErrorException
from pcs.lib.node import get_existing_nodes_names
from pcs.lib.pcs_cfgsync.save_sync import save_sync_new_known_hosts


def auth_hosts_token_no_sync(
    env: LibraryEnvironment, hosts: Mapping[str, HostWithTokenAuthData]
) -> None:
    """
    TODO
    """
    if env.report_processor.report_list(
        validations.validate_hosts_with_token(hosts)
    ).has_errors:
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

    known_hosts_facade.update_known_hosts(
        [
            host_data.to_known_host(host_name)
            for host_name, host_data in hosts.items()
        ]
    )
    if known_hosts_exists:
        known_hosts_facade.set_data_version(known_hosts_facade.data_version + 1)

    try:
        file_instance.write_facade(known_hosts_facade, can_overwrite=True)
    except RawFileError as e:
        env.report_processor.report(raw_file_error_report(e))
    if env.report_processor.has_errors:
        raise LibraryError()


def auth_hosts(
    env: LibraryEnvironment, hosts: Mapping[str, HostAuthData]
) -> None:
    """
    TODO
    """
    # TODO we want to raise LibraryError at the end, if there are any errors in processor
    # maybe report warning that something was done even though the command raised
    if env.report_processor.report_list(
        validations.validate_hosts(hosts)
    ).has_errors:
        raise LibraryError()

    node_communicator = env.get_node_communicator()
    com_cmd = Auth(hosts, env.report_processor)
    # we do not want to raise LibraryError in case only some nodes returned
    # errors, since we want to update the known-hosts file with whatever tokens
    # we were able to receive - this is how the old impl behaved
    # this means we cannot blindly check errors by using processor.has_errors
    received_tokens: dict[str, str] = run(node_communicator, com_cmd)

    new_known_hosts = [
        PcsKnownHost(
            name=host_name,
            token=received_tokens[host_name],
            dest_list=auth_data.dest_list,
        )
        for host_name, auth_data in hosts.items()
        if host_name in received_tokens
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
            sorted(set(node_names) - new_hosts_already_in_cluster)
        )  # type: ignore [no-untyped-call]
        target_list.extend(
            RequestTarget.from_known_host(host)
            for host in new_known_hosts
            if host.name in sorted(new_hosts_already_in_cluster)
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
