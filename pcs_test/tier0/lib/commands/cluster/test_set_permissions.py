from typing import Literal, Sequence
from unittest import TestCase

from pcs import settings
from pcs.common import file_type_codes, reports
from pcs.common.permissions.dto import PermissionEntryDto
from pcs.common.permissions.types import (
    PermissionAccessType,
    PermissionTargetType,
)
from pcs.lib.auth.const import SUPERUSER
from pcs.lib.commands import cluster
from pcs.lib.permissions.config.types import PermissionEntry

from pcs_test.tools import fixture
from pcs_test.tools.assertions import assert_report_item_list_equal
from pcs_test.tools.command_env import get_env_tools
from pcs_test.tools.fixture_pcs_cfgsync import (
    fixture_pcs_settings_file_content,
    fixture_save_sync_new_version_conflict,
    fixture_save_sync_new_version_error,
    fixture_save_sync_new_version_success,
)


class ValidateSetPermissions(TestCase):
    def call_validation(
        self, permissions: Sequence[PermissionEntryDto]
    ) -> reports.ReportItemList:
        return cluster._validate_set_permissions(permissions)

    def test_success(self):
        report_list = self.call_validation(
            [
                PermissionEntryDto(
                    "hacluster",
                    type=PermissionTargetType.USER,
                    allow=[
                        PermissionAccessType.FULL,
                        PermissionAccessType.SUPERUSER,
                    ],
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
        report_list = self.call_validation(
            [
                PermissionEntryDto(
                    "hacluster",
                    type=PermissionTargetType.USER,
                    allow=[
                        PermissionAccessType.FULL,
                        PermissionAccessType.SUPERUSER,
                    ],
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
        report_list = self.call_validation(
            [
                PermissionEntryDto(
                    "john",
                    type=PermissionTargetType.USER,
                    allow=[PermissionAccessType.SUPERUSER],
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


class SetPermissionsNotInCluster(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def test_input_validation_failed(self):
        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(),
                [PermissionEntryDto("", PermissionTargetType.USER, [])],
            )
        )
        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.INVALID_OPTION_VALUE,
                    option_name="name",
                    option_value="",
                    allowed_values=None,
                    cannot_be_empty=True,
                    forbidden_characters=None,
                )
            ]
        )

    def test_success_superuser_can_change_everything(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(
                1,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.WRITE],
                    )
                ],
            ),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            exists=False,
            name="corosync.exists",
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            fixture_pcs_settings_file_content(
                2,
                # check that the even the dependant permissions are written
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.READ],
                    ),
                    PermissionEntry(
                        "john",
                        PermissionTargetType.USER,
                        allow=[
                            PermissionAccessType.READ,
                            PermissionAccessType.WRITE,
                        ],
                    ),
                    PermissionEntry(
                        "admin",
                        PermissionTargetType.GROUP,
                        allow=[
                            PermissionAccessType.FULL,
                            PermissionAccessType.GRANT,
                            PermissionAccessType.READ,
                            PermissionAccessType.WRITE,
                        ],
                    ),
                    PermissionEntry(
                        "james",
                        PermissionTargetType.USER,
                        allow=sorted(set(PermissionAccessType)),
                    ),
                ],
            ).encode(),
            can_overwrite=True,
        )

        cluster.set_permissions(
            self.env_assist.get_env(user_login=SUPERUSER, user_groups=[]),
            [
                PermissionEntryDto(
                    "martin",
                    PermissionTargetType.USER,
                    [PermissionAccessType.READ],
                ),
                PermissionEntryDto(
                    "john",
                    PermissionTargetType.USER,
                    [PermissionAccessType.WRITE],
                ),
                PermissionEntryDto(
                    "admin",
                    PermissionTargetType.GROUP,
                    [PermissionAccessType.FULL],
                ),
                PermissionEntryDto(
                    "james",
                    PermissionTargetType.USER,
                    [PermissionAccessType.SUPERUSER],
                ),
            ],
        )

    def test_success_user_in_group_can_change_full_users(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(
                1,
                permissions=[
                    PermissionEntry(
                        "group",
                        PermissionTargetType.GROUP,
                        allow=[PermissionAccessType.FULL],
                    )
                ],
            ),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            exists=False,
            name="corosync.exists",
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "john",
                        PermissionTargetType.USER,
                        allow=[
                            PermissionAccessType.FULL,
                            PermissionAccessType.GRANT,
                            PermissionAccessType.READ,
                            PermissionAccessType.WRITE,
                        ],
                    ),
                ],
            ).encode(),
            can_overwrite=True,
        )

        cluster.set_permissions(
            self.env_assist.get_env(user_login="john", user_groups=["group"]),
            [
                PermissionEntryDto(
                    "john",
                    PermissionTargetType.USER,
                    [PermissionAccessType.FULL],
                ),
            ],
        )

    def test_user_has_no_permissions_to_change_full_users(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(
                1,
                permissions=[
                    PermissionEntry(
                        "john",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.WRITE],
                    )
                ],
            ),
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(
                    user_login="john", user_groups=["wheel"]
                ),
                [
                    PermissionEntryDto(
                        "james",
                        PermissionTargetType.USER,
                        [PermissionAccessType.FULL],
                    ),
                ],
            )
        )
        self.env_assist.assert_reports(
            [fixture.error(reports.codes.NOT_AUTHORIZED)]
        )

    def test_no_full_permissions_no_change_in_full_users(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(
                1,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[
                            PermissionAccessType.FULL,
                            PermissionAccessType.GRANT,
                            PermissionAccessType.READ,
                            PermissionAccessType.WRITE,
                        ],
                    )
                ],
            ),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            exists=False,
            name="corosync.exists",
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[
                            PermissionAccessType.FULL,
                            PermissionAccessType.GRANT,
                            PermissionAccessType.READ,
                            PermissionAccessType.WRITE,
                        ],
                    ),
                    PermissionEntry(
                        "group",
                        PermissionTargetType.GROUP,
                        allow=[PermissionAccessType.READ],
                    ),
                ],
            ).encode(),
            can_overwrite=True,
        )

        cluster.set_permissions(
            self.env_assist.get_env(user_login="john", user_groups=["wheel"]),
            [
                PermissionEntryDto(
                    "martin",
                    PermissionTargetType.USER,
                    [PermissionAccessType.FULL],
                ),
                PermissionEntryDto(
                    "group",
                    PermissionTargetType.GROUP,
                    [PermissionAccessType.READ],
                ),
            ],
        )

    def test_error_reading_pcs_settings_conf(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            exception_msg="Something bad",
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(),
                [
                    PermissionEntryDto(
                        "martin",
                        PermissionTargetType.USER,
                        [PermissionAccessType.READ],
                    )
                ],
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                    operation="read",
                    reason="Something bad",
                    file_path=settings.pcsd_settings_conf_location,
                )
            ]
        )

    def test_error_writing_pcs_settings_conf(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(1, permissions=[]),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            exists=False,
            name="corosync.exists",
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.READ],
                    )
                ],
            ).encode(),
            can_overwrite=True,
            exception_msg="Something bad",
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(),
                [
                    PermissionEntryDto(
                        "martin",
                        PermissionTargetType.USER,
                        [PermissionAccessType.READ],
                    ),
                ],
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                    operation="write",
                    reason="Something bad",
                    file_path=settings.pcsd_settings_conf_location,
                )
            ]
        )


class SetPermissionsInCluster(TestCase):
    NODE_LABELS = ["node1", "node2", "node3"]

    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)
        self.config.env.set_known_nodes(self.NODE_LABELS)

    def fixture_expected_cfgsync_reports(
        self, expected_result: Literal["ok", "conflict", "error"]
    ) -> reports.ReportItemList:
        _report_code_map = {
            "ok": reports.codes.PCS_CFGSYNC_CONFIG_ACCEPTED,
            "conflict": reports.codes.PCS_CFGSYNC_CONFIG_REJECTED,
            "error": reports.codes.PCS_CFGSYNC_CONFIG_SAVE_ERROR,
        }

        first_node_report = (
            fixture.error(
                _report_code_map[expected_result],
                file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                context=reports.dto.ReportItemContextDto(self.NODE_LABELS[0]),
            )
            if expected_result in ("error", "conflict")
            else fixture.info(
                _report_code_map[expected_result],
                file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                context=reports.dto.ReportItemContextDto(self.NODE_LABELS[0]),
            )
        )

        report_list = [
            fixture.info(
                reports.codes.PCS_CFGSYNC_SENDING_CONFIGS_TO_NODES,
                file_type_code_list=[file_type_codes.PCS_SETTINGS_CONF],
                node_name_list=self.NODE_LABELS,
            ),
            first_node_report,
        ] + [
            fixture.info(
                reports.codes.PCS_CFGSYNC_CONFIG_ACCEPTED,
                file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                context=reports.dto.ReportItemContextDto(node_label),
            )
            for node_label in self.NODE_LABELS[1:]
        ]

        if expected_result == "conflict":
            return report_list + [
                fixture.info(
                    reports.codes.PCS_CFGSYNC_FETCHING_NEWEST_CONFIG,
                    file_type_code_list=[file_type_codes.PCS_SETTINGS_CONF],
                    node_name_list=self.NODE_LABELS,
                ),
                fixture.error(reports.codes.PCS_CFGSYNC_CONFLICT_REPEAT_ACTION),
            ]

        if expected_result == "error":
            return report_list + [
                fixture.error(
                    reports.codes.PCS_CFGSYNC_SENDING_CONFIGS_TO_NODES_FAILED,
                    file_type_code_list=[file_type_codes.PCS_SETTINGS_CONF],
                    node_name_list=[self.NODE_LABELS[0]],
                )
            ]

        return report_list

    def test_success(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            name="corosync.exists",
        )
        self.config.corosync_conf.load(self.NODE_LABELS)
        fixture_save_sync_new_version_success(
            self.config,
            node_labels=self.NODE_LABELS,
            file_contents={
                file_type_codes.PCS_SETTINGS_CONF: fixture_pcs_settings_file_content(
                    2,
                    permissions=[
                        PermissionEntry(
                            "martin",
                            PermissionTargetType.USER,
                            allow=[PermissionAccessType.READ],
                        ),
                    ],
                )
            },
        )

        cluster.set_permissions(
            self.env_assist.get_env(user_login=SUPERUSER, user_groups=[]),
            [
                PermissionEntryDto(
                    "martin",
                    PermissionTargetType.USER,
                    [PermissionAccessType.READ],
                )
            ],
        )

        self.env_assist.assert_reports(
            self.fixture_expected_cfgsync_reports("ok")
        )

    def test_sync_conflict(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            name="corosync.exists",
        )
        self.config.corosync_conf.load(self.NODE_LABELS)
        cluster_newest_file = fixture_pcs_settings_file_content(
            data_version=300,
            permissions=[
                PermissionEntry(
                    "john",
                    PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                )
            ],
        )
        fixture_save_sync_new_version_conflict(
            self.config,
            node_labels=self.NODE_LABELS,
            file_type_code=file_type_codes.PCS_SETTINGS_CONF,
            local_file_content=fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.READ],
                    ),
                ],
            ),
            fetch_after_conflict=True,
            remote_file_content=cluster_newest_file,
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            cluster_newest_file.encode(),
            can_overwrite=True,
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(user_login=SUPERUSER, user_groups=[]),
                [
                    PermissionEntryDto(
                        "martin",
                        PermissionTargetType.USER,
                        [PermissionAccessType.READ],
                    )
                ],
            )
        )

        self.env_assist.assert_reports(
            self.fixture_expected_cfgsync_reports("conflict")
        )

    def test_sync_conflict_error_writing_newest_file(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            name="corosync.exists",
        )
        self.config.corosync_conf.load(self.NODE_LABELS)
        cluster_newest_file = fixture_pcs_settings_file_content(
            data_version=300,
            permissions=[
                PermissionEntry(
                    "john",
                    PermissionTargetType.USER,
                    allow=[PermissionAccessType.READ],
                )
            ],
        )
        fixture_save_sync_new_version_conflict(
            self.config,
            node_labels=self.NODE_LABELS,
            file_type_code=file_type_codes.PCS_SETTINGS_CONF,
            local_file_content=fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.READ],
                    ),
                ],
            ),
            fetch_after_conflict=True,
            remote_file_content=cluster_newest_file,
        )
        self.config.raw_file.write(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            cluster_newest_file.encode(),
            can_overwrite=True,
            exception_msg="Something bad",
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(user_login=SUPERUSER, user_groups=[]),
                [
                    PermissionEntryDto(
                        "martin",
                        PermissionTargetType.USER,
                        [PermissionAccessType.READ],
                    )
                ],
            )
        )

        self.env_assist.assert_reports(
            self.fixture_expected_cfgsync_reports("conflict")
            + [
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=file_type_codes.PCS_SETTINGS_CONF,
                    operation="write",
                    reason="Something bad",
                    file_path=settings.pcsd_settings_conf_location,
                ),
            ]
        )

    def test_sync_error(self):
        self.config.raw_file.exists(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
        )
        self.config.raw_file.read(
            file_type_codes.PCS_SETTINGS_CONF,
            settings.pcsd_settings_conf_location,
            content=fixture_pcs_settings_file_content(),
        )
        self.config.raw_file.exists(
            file_type_codes.COROSYNC_CONF,
            settings.corosync_conf_file,
            name="corosync.exists",
        )
        self.config.corosync_conf.load(self.NODE_LABELS)
        fixture_save_sync_new_version_error(
            self.config,
            node_labels=self.NODE_LABELS,
            file_type_code=file_type_codes.PCS_SETTINGS_CONF,
            local_file_content=fixture_pcs_settings_file_content(
                2,
                permissions=[
                    PermissionEntry(
                        "martin",
                        PermissionTargetType.USER,
                        allow=[PermissionAccessType.READ],
                    ),
                ],
            ),
        )

        self.env_assist.assert_raise_library_error(
            lambda: cluster.set_permissions(
                self.env_assist.get_env(user_login=SUPERUSER, user_groups=[]),
                [
                    PermissionEntryDto(
                        "martin",
                        PermissionTargetType.USER,
                        [PermissionAccessType.READ],
                    )
                ],
            )
        )

        self.env_assist.assert_reports(
            self.fixture_expected_cfgsync_reports("error")
        )
