import json
from unittest import TestCase

from pcs import settings
from pcs.common import reports
from pcs.common.file_type_codes import (
    COROSYNC_CONF,
    PCS_KNOWN_HOSTS,
    PCS_SETTINGS_CONF,
)
from pcs.common.pcs_cfgsync_dto import SyncConfigsDto
from pcs.lib.commands import pcs_cfgsync as lib

from pcs_test.tools import fixture
from pcs_test.tools.command_env import get_env_tools


class GetConfigs(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def fixture_files_operations(
        self, known_hosts_exists: bool = True, pcs_settings_exists: bool = True
    ) -> None:
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            exists=known_hosts_exists,
            name="known-hosts.exists",
        )
        if known_hosts_exists:
            self.config.raw_file.read(
                PCS_KNOWN_HOSTS,
                path=settings.pcsd_known_hosts_location,
                content="known-hosts content".encode("utf-8"),
                name="known-hosts.read",
            )
        self.config.raw_file.exists(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            exists=pcs_settings_exists,
            name="pcs_settings.exists",
        )
        if pcs_settings_exists:
            self.config.raw_file.read(
                PCS_SETTINGS_CONF,
                path=settings.pcsd_settings_conf_location,
                content="pcs_settings.conf content".encode("utf-8"),
                name="pcs_settings.read",
            )

    def test_bad_cluster_name(self):
        self.config.corosync_conf.load()
        self.env_assist.assert_raise_library_error(
            lambda: lib.get_configs(
                self.env_assist.get_env(), "definitely not the right name"
            )
        )
        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.NODE_REPORTS_UNEXPECTED_CLUSTER_NAME,
                    cluster_name="definitely not the right name",
                )
            ]
        )

    def test_not_in_cluster(self):
        # cant read corosync conf
        self.config.corosync_conf.load_content("", exception_msg="some error")
        self.env_assist.assert_raise_library_error(
            lambda: lib.get_configs(self.env_assist.get_env(), "test99"),
            reports=[
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=COROSYNC_CONF,
                    operation="read",
                    reason="some error",
                    file_path=settings.corosync_conf_file,
                )
            ],
            expected_in_processor=False,
        )

    def test_no_files_exist(self):
        self.config.corosync_conf.load()
        self.fixture_files_operations(
            known_hosts_exists=False, pcs_settings_exists=False
        )
        result = lib.get_configs(self.env_assist.get_env(), "test99")
        self.assertEqual(
            SyncConfigsDto(cluster_name="test99", configs={}), result
        )

    def test_some_files_exist(self):
        self.config.corosync_conf.load()
        self.fixture_files_operations(
            known_hosts_exists=True, pcs_settings_exists=False
        )
        result = lib.get_configs(self.env_assist.get_env(), "test99")
        self.assertEqual(
            SyncConfigsDto(
                cluster_name="test99",
                configs={PCS_KNOWN_HOSTS: "known-hosts content"},
            ),
            result,
        )

    def test_all_files_exist(self):
        self.config.corosync_conf.load()
        self.fixture_files_operations()
        result = lib.get_configs(self.env_assist.get_env(), "test99")
        self.assertEqual(
            SyncConfigsDto(
                cluster_name="test99",
                configs={
                    PCS_KNOWN_HOSTS: "known-hosts content",
                    PCS_SETTINGS_CONF: "pcs_settings.conf content",
                },
            ),
            result,
        )

    def test_raw_file_error(self):
        self.config.corosync_conf.load()
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            name="known-hosts.exists",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            exception_msg="some error",
            name="known-hosts.read",
        )
        self.config.raw_file.exists(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            name="pcs_settings.exists",
        )
        self.config.raw_file.read(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            content="pcs_settings.conf content".encode("utf-8"),
            name="pcs_settings.read",
        )

        result = lib.get_configs(self.env_assist.get_env(), "test99")

        self.assertEqual(
            SyncConfigsDto(
                cluster_name="test99",
                configs={PCS_SETTINGS_CONF: "pcs_settings.conf content"},
            ),
            result,
        )
        self.env_assist.assert_reports(
            [
                fixture.warn(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=PCS_KNOWN_HOSTS,
                    operation="read",
                    reason="some error",
                    file_path=settings.pcsd_known_hosts_location,
                )
            ]
        )


class SendLocalConfigsToClusterNodes(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    # TODO - reused from upthere
    def fixture_files_operations(
        self, known_hosts_exists: bool = True, pcs_settings_exists: bool = True
    ) -> None:
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            exists=known_hosts_exists,
            name="known-hosts.exists",
        )
        if known_hosts_exists:
            self.config.raw_file.read(
                PCS_KNOWN_HOSTS,
                path=settings.pcsd_known_hosts_location,
                content="known-hosts content".encode("utf-8"),
                name="known-hosts.read",
            )
        self.config.raw_file.exists(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            exists=pcs_settings_exists,
            name="pcs_settings.exists",
        )
        if pcs_settings_exists:
            self.config.raw_file.read(
                PCS_SETTINGS_CONF,
                path=settings.pcsd_settings_conf_location,
                content="pcs_settings.conf content".encode("utf-8"),
                name="pcs_settings.read",
            )

    def test_success(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations()
        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            node_labels=node_labels,
            file_contents={
                PCS_KNOWN_HOSTS: "known-hosts content",
                PCS_SETTINGS_CONF: "pcs_settings.conf content",
            },
        )

        lib.send_local_configs_to_cluster_nodes(self.env_assist.get_env())

    def test_success_force(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations()
        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            node_labels=node_labels,
            file_contents={
                PCS_KNOWN_HOSTS: "known-hosts content",
                PCS_SETTINGS_CONF: "pcs_settings.conf content",
            },
            force=True,
        )

        lib.send_local_configs_to_cluster_nodes(
            self.env_assist.get_env(), [reports.codes.FORCE]
        )

    def test_bad_cluster_name(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations()
        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            communication_list=[
                {
                    "label": "NODE-1",
                    "output": json.dumps({"status": "wrong_cluster_name"}),
                },
                {"label": "NODE-2"},
            ],
            file_contents={
                PCS_KNOWN_HOSTS: "known-hosts content",
                PCS_SETTINGS_CONF: "pcs_settings.conf content",
            },
        )

        self.env_assist.assert_raise_library_error(
            lambda: lib.send_local_configs_to_cluster_nodes(
                self.env_assist.get_env()
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.NODE_REPORTS_UNEXPECTED_CLUSTER_NAME,
                    cluster_name="test99",
                    context=reports.dto.ReportItemContextDto(node="NODE-1"),
                )
            ]
        )

    def test_error_from_remote(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations()
        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            file_contents={
                PCS_KNOWN_HOSTS: "known-hosts content",
                PCS_SETTINGS_CONF: "pcs_settings.conf content",
            },
            communication_list=[
                {
                    "label": "NODE-1",
                    "output": json.dumps(
                        {"status": "ok", "result": {PCS_KNOWN_HOSTS: "error"}}
                    ),
                },
                {"label": "NODE-2"},
            ],
        )

        self.env_assist.assert_raise_library_error(
            lambda: lib.send_local_configs_to_cluster_nodes(
                self.env_assist.get_env()
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.NODE_REPORTS_UNEXPECTED_CLUSTER_NAME,
                    cluster_name="test99",
                    context=reports.dto.ReportItemContextDto(node="NODE-1"),
                )
            ]
        )

    def test_no_local_files_to_send(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations(
            known_hosts_exists=False, pcs_settings_exists=False
        )

        lib.send_local_configs_to_cluster_nodes(self.env_assist.get_env())

    def test_error_reading_local_file(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            name="known-hosts.exists",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            path=settings.pcsd_known_hosts_location,
            exception_msg="some error",
            name="known-hosts.read",
        )
        self.config.raw_file.exists(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            name="pcs_settings.exists",
        )
        self.config.raw_file.read(
            PCS_SETTINGS_CONF,
            path=settings.pcsd_settings_conf_location,
            content="pcs_settings.conf content".encode("utf-8"),
            name="pcs_settings.read",
        )

        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            node_labels=node_labels,
            file_contents={PCS_SETTINGS_CONF: "pcs_settings.conf content"},
        )

        lib.send_local_configs_to_cluster_nodes(self.env_assist.get_env())

        self.env_assist.assert_reports(
            [
                fixture.warn(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=PCS_KNOWN_HOSTS,
                    operation="read",
                    reason="some error",
                    file_path=settings.pcsd_known_hosts_location,
                )
            ]
        )

    def test_no_corosync_conf(self):
        self.config.corosync_conf.load_content("", exception_msg="error")

        self.env_assist.assert_raise_library_error(
            lambda: lib.send_local_configs_to_cluster_nodes(
                self.env_assist.get_env()
            ),
            reports=[
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=COROSYNC_CONF,
                    operation="read",
                    reason="error",
                    file_path=settings.corosync_conf_file,
                )
            ],
            expected_in_processor=False,
        )

    def test_rejected_files_from_remote(self):
        node_labels = ["NODE-1", "NODE-2"]
        self.config.corosync_conf.load(node_name_list=node_labels)
        self.fixture_files_operations()
        self.config.env.set_known_nodes(node_labels)
        self.config.http.pcs_cfgsync.set_configs(
            file_contents={
                PCS_KNOWN_HOSTS: "known-hosts content",
                PCS_SETTINGS_CONF: "pcs_settings.conf content",
            },
            communication_list=[
                {
                    "label": "NODE-1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "NODE-2"},
            ],
        )

        self.env_assist.assert_raise_library_error(
            lambda: lib.send_local_configs_to_cluster_nodes(
                self.env_assist.get_env()
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.PCS_CFGSYNC_CONFIG_REJECTED,
                    file_type_code=PCS_KNOWN_HOSTS,
                    node_name_list=["NODE-1"],
                )
            ]
        )
