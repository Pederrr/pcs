from collections.abc import Callable
from dataclasses import dataclass

from pcs.common.host import PcsKnownHost
from pcs.common.reports import ReportProcessor


# TODO: reevaluate this whole class as we want to remove/change lib_wrapper
@dataclass
class Env:
    report_processor: ReportProcessor
    known_hosts_getter: Callable[[], dict[str, PcsKnownHost]]
    user: str | None = None
    groups: list[str] | None = None
    debug: bool = False
    request_timeout: int | None = None
    # TODO: types
    cib_data = None
    corosync_conf_data = None
    booth = None
    pacemaker = None
