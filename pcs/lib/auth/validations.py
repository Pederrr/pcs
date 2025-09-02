from dataclasses import asdict
from typing import Mapping, Sequence

from pcs.common import reports
from pcs.common.auth import HostAuthData, HostWithTokenAuthData
from pcs.common.host import Destination
from pcs.lib import validate


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


def validate_hosts_with_token(
    hosts: Mapping[str, HostWithTokenAuthData],
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
        report_list.extend(
            # - TODO - should we also check the upper bound of the token length ?
            #    - TODO - the tokens from --token are base64 encoded
            validate.ValueNotEmpty(
                "token", f"non-empty string for node '{host_name}'"
            ).validate(asdict(hosts[host_name]))
        )
        report_list.extend(
            _validate_destinations(host_name, hosts[host_name].dest_list)
        )

    return report_list


def validate_hosts(
    hosts: Mapping[str, HostAuthData],
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

        report_list.extend(
            _validate_destinations(host_name, hosts[host_name].dest_list)
        )

    return report_list
