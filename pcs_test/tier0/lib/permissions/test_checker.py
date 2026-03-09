from logging import Logger
from unittest import TestCase, mock

from pcs.lib.auth.const import SUPERUSER
from pcs.lib.auth.types import AuthUser
from pcs.lib.permissions.checker import (
    PermissionsChecker,
    get_local_cluster_permission_entries_with_allow_full,
)
from pcs.lib.permissions.config.facade import FacadeV2
from pcs.lib.permissions.config.types import (
    ClusterPermissions,
    ConfigV2,
    PermissionAccessType,
    PermissionEntry,
    PermissionTargetType,
)


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


_EMPTY_CONFIG = _config_fixture()


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
            set(PermissionAccessType),
            self.checker.get_permissions(
                AuthUser(username=SUPERUSER, groups=tuple())
            ),
        )

    def test_completion_full(self):
        self.assertEqual(
            {
                PermissionAccessType.READ,
                PermissionAccessType.WRITE,
                PermissionAccessType.GRANT,
                PermissionAccessType.FULL,
            },
            self.checker.get_permissions(
                AuthUser(username="user-full", groups=tuple())
            ),
        )

    def test_completion_write(self):
        self.assertEqual(
            {
                PermissionAccessType.READ,
                PermissionAccessType.WRITE,
            },
            self.checker.get_permissions(
                AuthUser(username="user-write", groups=tuple())
            ),
        )

    def test_groups_permissions(self):
        self.assertEqual(
            {PermissionAccessType.GRANT},
            self.checker.get_permissions(
                AuthUser(username="user", groups=("group-grant",))
            ),
        )

    def test_user_and_groups_permissions(self):
        self.assertEqual(
            {
                PermissionAccessType.READ,
                PermissionAccessType.WRITE,
                PermissionAccessType.GRANT,
            },
            self.checker.get_permissions(
                AuthUser(username="user-write", groups=("group-grant",))
            ),
        )

    def test_use_provided_facade(self):
        facade = FacadeV2(
            _config_fixture(
                (
                    PermissionEntry(
                        name="user",
                        type=PermissionTargetType.USER,
                        allow=(PermissionAccessType.READ,),
                    ),
                )
            )
        )
        self.assertEqual(
            {PermissionAccessType.READ},
            self.checker.get_permissions(
                AuthUser(username="user", groups=[]), facade=facade
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
        access = PermissionAccessType.READ
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
        access = PermissionAccessType.SUPERUSER
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
        access = PermissionAccessType.UNRESTRICTED
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

    def test_use_provided_facade(self):
        facade = FacadeV2(
            _config_fixture(
                (
                    PermissionEntry(
                        name="user",
                        type=PermissionTargetType.USER,
                        allow=(PermissionAccessType.READ,),
                    ),
                )
            )
        )
        user = AuthUser("user", ("group1", "group2"))
        access = PermissionAccessType.READ
        self.assertTrue(self.checker.is_authorized(user, access, facade))
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
                    str(PermissionAccessType.READ.value),
                ),
                mock.call("%s access granted", str(access.value).capitalize()),
            ],
            self.logger.debug.mock_calls,
        )


class GetLocalClusterPermissionEntriesWithAllowFull(TestCase):
    def test_success(self):
        result = get_local_cluster_permission_entries_with_allow_full(
            _FACADE_FIXTURE
        )

        self.assertEqual(
            result,
            [
                PermissionEntry(
                    "user-full",
                    PermissionTargetType.USER,
                    (PermissionAccessType.FULL,),
                )
            ],
        )

    def test_superuser(self):
        superuser_entry = PermissionEntry(
            "super",
            PermissionTargetType.USER,
            (PermissionAccessType.SUPERUSER,),
        )
        result = get_local_cluster_permission_entries_with_allow_full(
            FacadeV2(_config_fixture([superuser_entry]))
        )

        self.assertEqual(result, [superuser_entry])
