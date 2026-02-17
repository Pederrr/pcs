from dataclasses import dataclass
from enum import Enum
from typing import Collection

# from pcs.common.interface.dto import DataTransferObject


class PermissionTargetType(str, Enum):
    USER = "user"
    GROUP = "group"


class PermissionAccessType(str, Enum):
    UNRESTRICTED = "unrestricted"
    READ = "read"
    WRITE = "write"
    GRANT = "grant"
    FULL = "full"
    SUPERUSER = "superuser"


# TODO, we might just move the PermissionEntry from lib.permissions.config.types here, and make Dto from it
@dataclass(frozen=True)
class SetPermissionDto:
    name: str
    type: PermissionTargetType
    allow: Collection[PermissionAccessType]
