from dataclasses import dataclass

from pcs.common.interface.dto import DataTransferObject

from .types import PermissionAccessType, PermissionTargetType


@dataclass(frozen=True)
class PermissionEntryDto(DataTransferObject):
    name: str
    type: PermissionTargetType
    allow: list[PermissionAccessType]
