import logging

from pcs.common.permissions.types import (
    PermissionTargetType,
)
from pcs.lib.auth.const import SUPERUSER
from pcs.lib.auth.types import AuthUser

from .tools import complete_access_list, read_pcs_settings_conf
from .types import PermissionCheckAccessType


class PermissionsChecker:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _get_permissions(
        self, auth_user: AuthUser
    ) -> set[PermissionCheckAccessType]:
        if auth_user.username == SUPERUSER:
            return set(PermissionCheckAccessType) - {
                PermissionCheckAccessType.NONE
            }

        facade = read_pcs_settings_conf(self._logger)[0]
        all_permissions = set()
        for target_name, target_type in [
            (auth_user.username, PermissionTargetType.USER)
        ] + [(group, PermissionTargetType.GROUP) for group in auth_user.groups]:
            entry = facade.get_entry(target_name, target_type)
            if entry:
                all_permissions |= set(entry.allow)
        return {
            PermissionCheckAccessType.from_permission_access_type(allow)
            for allow in complete_access_list(all_permissions)
        }

    def is_authorized(
        self, auth_user: AuthUser, access: PermissionCheckAccessType
    ) -> bool:
        self._logger.debug(
            "Permission check: username=%s groups=%s access=%s",
            auth_user.username,
            ",".join(auth_user.groups),
            str(access.value),
        )
        if access is PermissionCheckAccessType.NONE:
            # We dont need to read the permissions file in this case
            result = True
        else:
            user_permissions = self._get_permissions(auth_user)
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
