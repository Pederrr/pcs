from typing import Any, Mapping, Sequence, Union, cast

from dacite import from_dict

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


def _validate_hosts(
    hosts: Mapping[str, Sequence[Mapping[str, Union[str, int]]]],
) -> reports.ReportItemList:
    report_list = []
    if not hosts:
        report_list.append(
            reports.ReportItem.error(reports.messages.NoHostSpecified())
        )
    for host_name in hosts:
        report_list.extend(
            validate.ValueNotEmptyNotNone("host name", "").validate(
                {"host name": host_name}
            )
        )

        dest_list = hosts[host_name]
        if not isinstance(dest_list, list) or not dest_list:
            report_list.append(
                reports.ReportItem.error(
                    reports.messages.InvalidOptionValue(
                        "dest_list",
                        str(dest_list),
                        "non-empty list of destinations",
                        cannot_be_empty=True,
                    )
                )
            )
        for destination in dest_list:
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
                ).validate(destination)
            )

    return report_list


def auth_hosts_token_no_sync(
    env: LibraryEnvironment,
    hosts_dict: Mapping[str, Any],  # TODO types
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


def auth_hosts(
    env: LibraryEnvironment,
    username: str,
    password: str,
    hosts: Mapping[str, Sequence[Mapping[str, Union[str, int]]]],
) -> None:
    if env.report_processor.report_list(_validate_hosts(hosts)).has_errors:
        raise LibraryError()

    request_targets = [
        RequestTarget(
            label=host_name,
            dest_list=[from_dict(Destination, dest) for dest in destinations],
        )
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
