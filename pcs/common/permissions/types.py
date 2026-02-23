from dataclasses import dataclass
from enum import Enum
from typing import Collection


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


@dataclass(frozen=True)
class SetPermissionsDto:
    name: str
    type: PermissionTargetType
    allow: Collection[PermissionAccessType]
