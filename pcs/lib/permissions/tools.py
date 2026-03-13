from typing import Collection

from pcs.common.permissions.types import PermissionAccessType


def complete_access_list(
    access_list: Collection[PermissionAccessType],
) -> set[PermissionAccessType]:
    if PermissionAccessType.FULL in access_list:
        return set(PermissionAccessType)

    permission_set = set(access_list)
    if PermissionAccessType.WRITE in permission_set:
        return permission_set | {PermissionAccessType.READ}
    return permission_set
