import logging
from typing import Collection, Optional

from pcs.lib.auth.const import SUPERUSER
from pcs.lib.auth.types import AuthUser

from .config.facade import FacadeV2
from .config.types import (
    PermissionAccessType,
    PermissionEntry,
    PermissionTargetType,
)
from .tools import read_pcs_settings_conf


def complete_access_list(
    access_list: Collection[PermissionAccessType],
) -> set[PermissionAccessType]:
    if PermissionAccessType.SUPERUSER in access_list:
        return set(PermissionAccessType)
    if PermissionAccessType.FULL in access_list:
        return {
            PermissionAccessType.READ,
            PermissionAccessType.WRITE,
            PermissionAccessType.GRANT,
            PermissionAccessType.FULL,
        }
    new = set(access_list)
    if PermissionAccessType.WRITE in access_list:
        return new | {PermissionAccessType.READ}
    return new


def get_local_cluster_permission_entries_with_allow_full(
    facade: FacadeV2,
) -> list[PermissionEntry]:
    return [
        entry
        for entry in facade.config.permissions.local_cluster
        if PermissionAccessType.FULL in complete_access_list(entry.allow)
    ]


class PermissionsChecker:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def get_permissions(
        self, auth_user: AuthUser, facade: Optional[FacadeV2] = None
    ) -> set[PermissionAccessType]:
        if auth_user.username == SUPERUSER:
            return complete_access_list((PermissionAccessType.SUPERUSER,))
        facade = (
            facade
            if facade is not None
            else read_pcs_settings_conf(self._logger)[0]
        )
        all_permissions: set[PermissionAccessType] = set()
        for target_name, target_type in [
            (auth_user.username, PermissionTargetType.USER)
        ] + [(group, PermissionTargetType.GROUP) for group in auth_user.groups]:
            entry = facade.get_entry(target_name, target_type)
            if entry:
                all_permissions |= set(entry.allow)
        return complete_access_list(all_permissions)

    def is_authorized(
        self,
        auth_user: AuthUser,
        access: PermissionAccessType,
        facade: Optional[FacadeV2] = None,
    ) -> bool:
        self._logger.debug(
            "Permission check: username=%s groups=%s access=%s",
            auth_user.username,
            ",".join(auth_user.groups),
            str(access.value),
        )
        if access is PermissionAccessType.UNRESTRICTED:
            result = True
        else:
            user_permissions = self.get_permissions(auth_user, facade)
            self._logger.debug(
                "Current user permissions: %s",
                ",".join(
                    sorted(permission.value for permission in user_permissions)
                ),
            )
            result = access in user_permissions
        if result:
            self._logger.debug(
                "%s access granted", str(access.value).capitalize()
            )
        else:
            self._logger.debug(
                "%s access denied", str(access.value).capitalize()
            )
        return result
