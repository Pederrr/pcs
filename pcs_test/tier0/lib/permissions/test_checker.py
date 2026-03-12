from logging import Logger
from unittest import TestCase, mock

from pcs.lib.auth.const import SUPERUSER
from pcs.lib.auth.types import AuthUser
from pcs.lib.permissions.checker import PermissionsChecker
from pcs.lib.permissions.config.facade import FacadeV2
from pcs.lib.permissions.config.types import (
    ClusterPermissions,
    ConfigV2,
    PermissionAccessType,
    PermissionEntry,
    PermissionTargetType,
)
from pcs.lib.permissions.types import PermissionCheckAccessType


def _config_fixture(permissions=tuple()):
    return ConfigV2(
        data_version=1,
        clusters=[],
        permissions=ClusterPermissions(local_cluster=permissions),
    )


_FACADE_FIXTURE = FacadeV2(
    _config_fixture(
        (
            PermissionEntry(
                name="user-read",
                type=PermissionTargetType.USER,
                allow=(PermissionAccessType.READ,),
            ),
            PermissionEntry(
                name="user-write",
                type=PermissionTargetType.USER,
                allow=(PermissionAccessType.WRITE,),
            ),
            PermissionEntry(
                name="user-full",
                type=PermissionTargetType.USER,
                allow=(PermissionAccessType.FULL,),
            ),
            PermissionEntry(
                name="group-grant",
                type=PermissionTargetType.GROUP,
                allow=(PermissionAccessType.GRANT,),
            ),
        )
    )
)


@mock.patch(
    "pcs.lib.permissions.checker.read_pcs_settings_conf",
    lambda _logger: (_FACADE_FIXTURE, []),
)
class PermissionCheckerGetPermissions(TestCase):
    def setUp(self):
        self.logger = mock.Mock(spec_set=Logger)
        self.checker = PermissionsChecker(self.logger)

    def test_superuser(self):
        self.assertEqual(
            {
                PermissionCheckAccessType.READ,
                PermissionCheckAccessType.WRITE,
                PermissionCheckAccessType.GRANT,
                PermissionCheckAccessType.FULL,
                PermissionCheckAccessType.SUPERUSER,
            },
            self.checker._get_permissions(
                AuthUser(username=SUPERUSER, groups=tuple())
            ),
        )

    def test_completion_full(self):
        self.assertEqual(
            {
                PermissionCheckAccessType.READ,
                PermissionCheckAccessType.WRITE,
                PermissionCheckAccessType.GRANT,
                PermissionCheckAccessType.FULL,
            },
            self.checker._get_permissions(
                AuthUser(username="user-full", groups=tuple())
            ),
        )

    def test_completion_write(self):
        self.assertEqual(
            {
                PermissionCheckAccessType.READ,
                PermissionCheckAccessType.WRITE,
            },
            self.checker._get_permissions(
                AuthUser(username="user-write", groups=tuple())
            ),
        )

    def test_groups_permissions(self):
        self.assertEqual(
            {PermissionCheckAccessType.GRANT},
            self.checker._get_permissions(
                AuthUser(username="user", groups=("group-grant",))
            ),
        )

    def test_user_and_groups_permissions(self):
        self.assertEqual(
            {
                PermissionCheckAccessType.READ,
                PermissionCheckAccessType.WRITE,
                PermissionCheckAccessType.GRANT,
            },
            self.checker._get_permissions(
                AuthUser(username="user-write", groups=("group-grant",))
            ),
        )


@mock.patch(
    "pcs.lib.permissions.checker.read_pcs_settings_conf",
    lambda _logger: (_FACADE_FIXTURE, []),
)
class PermissionsCheckerIsAuthorizedTest(TestCase):
    def setUp(self):
        self.logger = mock.Mock(spec_set=Logger)
        self.checker = PermissionsChecker(self.logger)

    def test_allowed(self):
        user = AuthUser("user-full", ("group1", "group2"))
        access = PermissionCheckAccessType.READ
        self.assertTrue(self.checker.is_authorized(user, access))
        self.assertEqual(
            [
                mock.call(
                    "Permission check: username=%s groups=%s access=%s",
                    user.username,
                    ",".join(user.groups),
                    str(access.value),
                ),
                mock.call(
                    "Current user permissions: %s",
                    ",".join(
                        sorted(
                            str(permission.value)
                            for permission in (
                                PermissionAccessType.READ,
                                PermissionAccessType.WRITE,
                                PermissionAccessType.GRANT,
                                PermissionAccessType.FULL,
                            )
                        )
                    ),
                ),
                mock.call("%s access granted", str(access.value).capitalize()),
            ],
            self.logger.debug.mock_calls,
        )

    def test_not_allowed(self):
        user = AuthUser("user-full", ("group1", "group2"))
        access = PermissionCheckAccessType.SUPERUSER
        self.assertFalse(self.checker.is_authorized(user, access))
        self.assertEqual(
            [
                mock.call(
                    "Permission check: username=%s groups=%s access=%s",
                    user.username,
                    ",".join(user.groups),
                    str(access.value),
                ),
                mock.call(
                    "Current user permissions: %s",
                    ",".join(
                        sorted(
                            str(permission.value)
                            for permission in (
                                PermissionAccessType.READ,
                                PermissionAccessType.WRITE,
                                PermissionAccessType.GRANT,
                                PermissionAccessType.FULL,
                            )
                        )
                    ),
                ),
                mock.call("%s access denied", str(access.value).capitalize()),
            ],
            self.logger.debug.mock_calls,
        )

    def test_unrestricted(self):
        user = AuthUser("user-full", ("group1", "group2"))
        access = PermissionCheckAccessType.NONE
        self.assertTrue(self.checker.is_authorized(user, access))
        self.assertEqual(
            [
                mock.call(
                    "Permission check: username=%s groups=%s access=%s",
                    user.username,
                    ",".join(user.groups),
                    str(access.value),
                ),
                mock.call("%s access granted", str(access.value).capitalize()),
            ],
            self.logger.debug.mock_calls,
        )
