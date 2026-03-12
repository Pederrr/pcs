from unittest import TestCase

from pcs.common.permissions.types import PermissionAccessType
from pcs.lib.permissions.tools import complete_access_list


class CompleteAccessList(TestCase):
    def test_combinations(self):
        combinations = (
            (
                [PermissionAccessType.READ, PermissionAccessType.GRANT],
                {PermissionAccessType.READ, PermissionAccessType.GRANT},
            ),
            (
                [PermissionAccessType.WRITE, PermissionAccessType.GRANT],
                {
                    PermissionAccessType.READ,
                    PermissionAccessType.WRITE,
                    PermissionAccessType.GRANT,
                },
            ),
            (
                [PermissionAccessType.FULL],
                {
                    PermissionAccessType.READ,
                    PermissionAccessType.WRITE,
                    PermissionAccessType.GRANT,
                    PermissionAccessType.FULL,
                },
            ),
        )

        for access_list, expected_result in combinations:
            with self.subTest(value=access_list):
                self.assertEqual(
                    complete_access_list(access_list), expected_result
                )
