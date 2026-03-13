from unittest import TestCase

from pcs.common import reports
from pcs.common.permissions.dto import PermissionEntryDto
from pcs.common.permissions.types import (
    PermissionAccessType,
    PermissionTargetType,
)
from pcs.lib.permissions import validations

from pcs_test.tools import fixture
from pcs_test.tools.assertions import assert_report_item_list_equal


class ValidateSetPermissions(TestCase):
    def test_success(self):
        report_list = validations.validate_set_permissions(
            [
                PermissionEntryDto(
                    "hacluster",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.FULL],
                ),
                PermissionEntryDto(
                    "haclient",
                    type=PermissionTargetType.GROUP,
                    allow=[
                        PermissionAccessType.GRANT,
                        PermissionAccessType.READ,
                        PermissionAccessType.WRITE,
                    ],
                ),
            ]
        )

        assert_report_item_list_equal(report_list, [])

    def test_empty_name(self):
        report_list = validations.validate_set_permissions(
            [
                PermissionEntryDto(
                    "hacluster",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.FULL],
                ),
                PermissionEntryDto(
                    "",
                    type=PermissionTargetType.GROUP,
                    allow=[PermissionAccessType.READ],
                ),
            ]
        )

        assert_report_item_list_equal(
            report_list,
            [
                fixture.error(
                    reports.codes.INVALID_OPTION_VALUE,
                    option_name="name",
                    option_value="",
                    allowed_values=None,
                    cannot_be_empty=True,
                    forbidden_characters=None,
                )
            ],
        )

    def test_duplicates(self):
        report_list = validations.validate_set_permissions(
            [
                PermissionEntryDto(
                    "john",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.GRANT],
                ),
                PermissionEntryDto(
                    "john",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.WRITE],
                ),
                # same name, but different 'type' are not duplicates
                PermissionEntryDto(
                    "martin",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                ),
                PermissionEntryDto(
                    "martin",
                    type=PermissionTargetType.GROUP,
                    allow=[PermissionAccessType.READ],
                ),
            ]
        )

        assert_report_item_list_equal(
            report_list,
            [
                fixture.error(
                    reports.codes.PERMISSION_DUPLICATION,
                    target_list=[("john", PermissionTargetType.USER)],
                )
            ],
        )
