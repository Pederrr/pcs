from enum import Enum

from pcs.common.permissions.types import PermissionAccessType


class PermissionCheckAccessType(str, Enum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    GRANT = "grant"
    FULL = "full"
    SUPERUSER = "superuser"

    @classmethod
    def from_permission_access_type(
        cls,
        permission_access_type: PermissionAccessType,
    ) -> "PermissionCheckAccessType":
        mapping = {
            PermissionAccessType.READ: cls.READ,
            PermissionAccessType.WRITE: cls.WRITE,
            PermissionAccessType.GRANT: cls.GRANT,
            PermissionAccessType.FULL: cls.FULL,
        }
        return mapping[permission_access_type]
