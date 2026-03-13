from typing import Sequence

from pcs.common.permissions.dto import PermissionEntryDto
from pcs.lib.auth.types import AuthUser
from pcs.lib.permissions.checker import PermissionsChecker
from pcs.lib.permissions.config.facade import FacadeV2
from pcs.lib.permissions.config.types import (
    PermissionAccessType,
    PermissionEntry,
)
from pcs.lib.permissions.tools import complete_access_list
from pcs.lib.permissions.types import PermissionCheckAccessType


class NotAuthorizedToChangeFullUsersException(Exception):
    pass


def prepare_set_permissions(
    permissions: Sequence[PermissionEntryDto],
    pcs_settings: FacadeV2,
    auth_user: AuthUser,
    permissions_checker: PermissionsChecker,
) -> list[PermissionEntry]:
    new_full_users = set()
    new_permission_list = []
    for perm in permissions:
        if PermissionAccessType.FULL in perm.allow:
            new_full_users.add((perm.name, perm.type))
        # Explicitly save dependant permissions. That way if the dependency is
        # changed in the future, it won't revoke permissions which were once
        # granted
        allow = complete_access_list(set(perm.allow))
        new_permission_list.append(
            PermissionEntry(name=perm.name, type=perm.type, allow=sorted(allow))
        )

    current_full_users = {
        (perm.name, perm.type)
        for perm in pcs_settings.get_entries_with_allow_full()
    }
    if new_full_users != current_full_users:
        if not permissions_checker.is_authorized(
            auth_user, PermissionCheckAccessType.FULL
        ):
            raise NotAuthorizedToChangeFullUsersException()

    return new_permission_list
