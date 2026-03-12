from unittest import TestCase, mock

from pcs.common.permissions.dto import PermissionEntryDto
from pcs.common.permissions.types import (
    PermissionAccessType,
    PermissionTargetType,
)
from pcs.lib.auth.types import AuthUser
from pcs.lib.permissions import operations
from pcs.lib.permissions.checker import PermissionsChecker
from pcs.lib.permissions.config.facade import FacadeV2
from pcs.lib.permissions.config.types import (
    ClusterPermissions,
    ConfigV2,
    PermissionEntry,
)


class PrepareSetPermissions(TestCase):
    def setUp(self):
        self.permissions_checker = mock.Mock(spec_set=PermissionsChecker)
        self.auth_user = AuthUser("user", ["haclient"])

    def test_success_basic_permissions(self):
        new_permissions = operations.prepare_set_permissions(
            [
                PermissionEntryDto(
                    "martin",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                ),
                PermissionEntryDto(
                    "jozef",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.GRANT],
                ),
                PermissionEntryDto(
                    "users",
                    type=PermissionTargetType.GROUP,
                    allow=[PermissionAccessType.WRITE],
                ),
            ],
            FacadeV2.create(),
            self.auth_user,
            self.permissions_checker,
        )

        self.assertEqual(
            new_permissions,
            [
                PermissionEntry(
                    name="martin",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                ),
                PermissionEntry(
                    name="jozef",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.GRANT],
                ),
                PermissionEntry(
                    name="users",
                    type=PermissionTargetType.GROUP,
                    allow=[
                        PermissionAccessType.READ,
                        PermissionAccessType.WRITE,
                    ],
                ),
            ],
        )

    def test_full_permissions_no_change(self):
        facade = FacadeV2(
            ConfigV2(
                data_version=1,
                clusters=[],
                permissions=ClusterPermissions(
                    [
                        PermissionEntry(
                            name="martin",
                            type=PermissionTargetType.USER,
                            allow=[PermissionAccessType.FULL],
                        )
                    ]
                ),
            )
        )

        new_permissions = operations.prepare_set_permissions(
            [
                PermissionEntryDto(
                    "jozef",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                ),
                PermissionEntryDto(
                    "martin",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.FULL],
                ),
            ],
            facade,
            self.auth_user,
            self.permissions_checker,
        )

        self.assertEqual(
            new_permissions,
            [
                PermissionEntry(
                    name="jozef",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                ),
                PermissionEntry(
                    name="martin",
                    type=PermissionTargetType.USER,
                    allow=[
                        PermissionAccessType.FULL,
                        PermissionAccessType.GRANT,
                        PermissionAccessType.READ,
                        PermissionAccessType.WRITE,
                    ],
                ),
            ],
        )

    def test_full_permissions_success(self):
        self.permissions_checker.is_authorized.return_value = True
        facade = FacadeV2(
            ConfigV2(
                data_version=1,
                clusters=[],
                permissions=ClusterPermissions(
                    [
                        PermissionEntry(
                            name="user",
                            type=PermissionTargetType.USER,
                            allow=[PermissionAccessType.FULL],
                        )
                    ]
                ),
            )
        )

        new_permissions = operations.prepare_set_permissions(
            [
                PermissionEntryDto(
                    "martin",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.FULL],
                ),
            ],
            facade,
            self.auth_user,
            self.permissions_checker,
        )

        self.assertEqual(
            new_permissions,
            [
                PermissionEntry(
                    name="martin",
                    type=PermissionTargetType.USER,
                    allow=[
                        PermissionAccessType.FULL,
                        PermissionAccessType.GRANT,
                        PermissionAccessType.READ,
                        PermissionAccessType.WRITE,
                    ],
                ),
            ],
        )

    def test_full_permissions_raise(self):
        self.permissions_checker.is_authorized.return_value = False
        facade = FacadeV2(
            ConfigV2(
                data_version=1,
                clusters=[],
                permissions=ClusterPermissions(
                    [
                        PermissionEntry(
                            name="user",
                            type=PermissionTargetType.USER,
                            allow=[PermissionAccessType.READ],
                        )
                    ]
                ),
            )
        )

        with self.assertRaises(
            operations.NotAuthorizedToChangeFullUsersException
        ):
            operations.prepare_set_permissions(
                [
                    PermissionEntryDto(
                        "martin",
                        type=PermissionTargetType.USER,
                        allow=[PermissionAccessType.FULL],
                    ),
                ],
                facade,
                self.auth_user,
                self.permissions_checker,
            )
