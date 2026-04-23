from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from pcs.common.interface.dto import DataTransferObject
from pcs.common.reports.dto import ReportItemDto

from .types import (
    TaskFinishType,
    TaskKillReason,
    TaskState,
)


@dataclass(frozen=True)
class CommandOptionsDto(DataTransferObject):
    request_timeout: Optional[int] = None
    effective_username: Optional[str] = None
    effective_groups: Optional[List[str]] = None


@dataclass(frozen=True)
class CommandDto(DataTransferObject):
    command_name: str
    params: Dict[str, Any]
    options: CommandOptionsDto


@dataclass(frozen=True)
class TaskIdentDto(DataTransferObject):
    task_ident: str


@dataclass(frozen=True)
class TaskResultDto(DataTransferObject):
    task_ident: str
    command: CommandDto
    reports: List[ReportItemDto]
    state: TaskState
    task_finish_type: TaskFinishType
    kill_reason: Optional[TaskKillReason]
    # TODO
    # there is a whole parostroj for converting DTOs implemented in
    # pcs.common.interface.dto.to_dict but it is actually not used for result
    # DTO data because this is typed as Any. However, the commands with DTOs
    # as output still work without any issues -> dataclasses.asdict does
    # everything we are trying to achieve
    #
    # e.g. lib command status.resources_status command works without any issues
    # despite the output value being defined as bunch of Unions (which are
    # dissalowed in our conversion steam engine)
    result: Any
