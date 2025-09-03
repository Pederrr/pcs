import json
from typing import Optional
from unittest import TestCase

from pcs import settings
from pcs.common import reports
from pcs.common.auth import HostAuthData, HostWithTokenAuthData
from pcs.common.communication.const import COM_STATUS_SUCCESS
from pcs.common.communication.dto import InternalCommunicationResultDto
from pcs.common.communication.types import CommunicationResultStatus
from pcs.common.file_type_codes import COROSYNC_CONF, PCS_KNOWN_HOSTS
from pcs.common.host import Destination, PcsKnownHost
from pcs.common.interface.dto import to_dict
from pcs.lib.commands import auth
from pcs.lib.host.config.exporter import Exporter as KnownHostsExporter
from pcs.lib.host.config.types import KnownHosts

from pcs_test.tools import fixture
from pcs_test.tools.command_env import get_env_tools


def fixture_known_hosts_file_content(
    data_version, hosts: dict[str, PcsKnownHost]
) -> str:
    return KnownHostsExporter.export(
        KnownHosts(
            format_version=1, data_version=data_version, known_hosts=hosts
        )
    ).decode("utf-8")


def fixture_communication_result_string(
    status: CommunicationResultStatus = COM_STATUS_SUCCESS,
    status_msg: Optional[str] = None,
    report_list: Optional[reports.dto.ReportItemDto] = None,
    data="",
) -> str:
    return json.dumps(
        to_dict(
            InternalCommunicationResultDto(
                status=status,
                status_msg=status_msg,
                report_list=report_list or [],
                data=data,
            )
        )
    )


_FIXTURE_KNOWN_HOSTS = {
    "node1": PcsKnownHost("node1", "aaa", [Destination("node1", 2224)]),
    "node2": PcsKnownHost("node2", "bbb", [Destination("node2", 2224)]),
}


class AuthHosts(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def fixture_send_new_tokens_in_cluster(
        self, new_tokens: dict[str, PcsKnownHost]
    ):
        self.config.raw_file.exists(
            COROSYNC_CONF, settings.corosync_conf_file, name="corosync.exists"
        )
        self.config.env.set_known_nodes(["node1", "node2"])
        self.config.corosync_conf.load(["node1", "node2"])
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="known_hosts.exists",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1, _FIXTURE_KNOWN_HOSTS),
        )
        self.config.http.pcs_cfgsync.set_configs(
            "test99",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    2, _FIXTURE_KNOWN_HOSTS | new_tokens
                )
            },
            node_labels=["node1", "node2"],
        )

    def test_success_not_in_cluster(self):
        self.config.http.place_multinode_call(
            node_labels=["node3"],
            output="TOKEN",
            action="remote/auth",
            param_list=[("username", "username"), ("password", "password")],
            name="auth",
        )
        self.config.raw_file.exists(
            COROSYNC_CONF,
            settings.corosync_conf_file,
            exists=False,
            name="corosync.exists",
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="known_hosts.exists",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1, _FIXTURE_KNOWN_HOSTS),
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            file_data=fixture_known_hosts_file_content(
                2,
                _FIXTURE_KNOWN_HOSTS
                | {
                    "node3": PcsKnownHost(
                        "node3", "TOKEN", [Destination("node3", 2224)]
                    )
                },
            ).encode("utf-8"),
            can_overwrite=True,
        )

        auth.auth_hosts(
            self.env_assist.get_env(),
            {
                "node3": HostAuthData(
                    "username", "password", [Destination("node3", 2224)]
                )
            },
        )

        self.env_assist.assert_reports(
            [
                fixture.info(
                    reports.codes.AUTHORIZATION_SUCCESSFUL,
                    context=reports.dto.ReportItemContextDto("node3"),
                )
            ]
        )

    def test_success_in_cluster(self):
        self.config.http.place_multinode_call(
            node_labels=["node3", "node4"],
            output="TOKEN",
            action="remote/auth",
            param_list=[("username", "username"), ("password", "password")],
            name="auth",
        )
        self.fixture_send_new_tokens_in_cluster(
            {
                "node3": PcsKnownHost(
                    "node3", "TOKEN", [Destination("node3", 2224)]
                ),
                "node4": PcsKnownHost(
                    "node4", "TOKEN", [Destination("node4", 2224)]
                ),
            }
        )

        auth.auth_hosts(
            self.env_assist.get_env(),
            {
                "node3": HostAuthData(
                    "username", "password", [Destination("node3", 2224)]
                ),
                "node4": HostAuthData(
                    "username", "password", [Destination("node4", 2224)]
                ),
            },
        )

        self.env_assist.assert_reports(
            [
                fixture.info(
                    reports.codes.AUTHORIZATION_SUCCESSFUL,
                    context=reports.dto.ReportItemContextDto("node3"),
                ),
                fixture.info(
                    reports.codes.AUTHORIZATION_SUCCESSFUL,
                    context=reports.dto.ReportItemContextDto("node4"),
                ),
            ]
        )

    def test_success_only_able_to_auth_some_nodes(self):
        self.config.http.place_multinode_call(
            communication_list=[
                {"label": "node3", "output": "TOKEN"},
                {"label": "node4", "output": ""},
            ],
            action="remote/auth",
            param_list=[("username", "username"), ("password", "password")],
            name="auth",
        )
        self.fixture_send_new_tokens_in_cluster(
            {
                "node3": PcsKnownHost(
                    "node3", "TOKEN", [Destination("node3", 2224)]
                )
            }
        )

        auth.auth_hosts(
            self.env_assist.get_env(),
            {
                "node3": HostAuthData(
                    "username", "password", [Destination("node3", 2224)]
                ),
                "node4": HostAuthData(
                    "username", "password", [Destination("node4", 2224)]
                ),
            },
        )

        self.env_assist.assert_reports(
            [
                fixture.info(
                    reports.codes.AUTHORIZATION_SUCCESSFUL,
                    context=reports.dto.ReportItemContextDto("node3"),
                ),
                fixture.error(
                    reports.codes.INCORRECT_CREDENTIALS,
                    context=reports.dto.ReportItemContextDto("node4"),
                ),
            ]
        )

    def test_unable_to_auth_any_host(self):
        self.config.http.place_multinode_call(
            node_labels=["node3", "node4"],
            output="",  # empty token means that auth was not succesfull
            action="remote/auth",
            param_list=[("username", "username"), ("password", "password")],
            name="auth",
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts(
                self.env_assist.get_env(),
                {
                    "node3": HostAuthData(
                        "username", "password", [Destination("node3", 2224)]
                    ),
                    "node4": HostAuthData(
                        "username", "password", [Destination("node4", 2224)]
                    ),
                },
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.INCORRECT_CREDENTIALS,
                    context=reports.dto.ReportItemContextDto("node3"),
                ),
                fixture.error(
                    reports.codes.INCORRECT_CREDENTIALS,
                    context=reports.dto.ReportItemContextDto("node4"),
                ),
            ]
        )


class AuthHostsTokenNoSync(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def test_adds_new(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1, _FIXTURE_KNOWN_HOSTS),
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            fixture_known_hosts_file_content(
                2,
                {
                    **_FIXTURE_KNOWN_HOSTS,
                    "node3": PcsKnownHost(
                        "node3", "TOKEN", [Destination("node3", 2224)]
                    ),
                    "node4": PcsKnownHost(
                        "node4", "TOKEN", [Destination("node4", 2224)]
                    ),
                },
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(),
            {
                "node3": HostWithTokenAuthData(
                    "TOKEN", [Destination("node3", 2224)]
                ),
                "node4": HostWithTokenAuthData(
                    "TOKEN", [Destination("node4", 2224)]
                ),
            },
        )

    def test_adds_new_rewrites_existing_host(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1, _FIXTURE_KNOWN_HOSTS),
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            fixture_known_hosts_file_content(
                2,
                {
                    **_FIXTURE_KNOWN_HOSTS,
                    "node2": PcsKnownHost(
                        "node2", "TOKEN", [Destination("node22", 8080)]
                    ),
                    "node3": PcsKnownHost(
                        "node3", "TOKEN", [Destination("node3", 2224)]
                    ),
                },
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(),
            {
                "node2": HostWithTokenAuthData(
                    "TOKEN", [Destination("node22", 8080)]
                ),
                "node3": HostWithTokenAuthData(
                    "TOKEN", [Destination("node3", 2224)]
                ),
            },
        )

    def test_invalid_data_empty_token(self):
        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(
                self.env_assist.get_env(),
                {
                    "node2": HostWithTokenAuthData(
                        "", [Destination("node2", 2224)]
                    )
                },
            )
        )
        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.INVALID_OPTION_VALUE,
                    option_name="token",
                    option_value="",
                    allowed_values="non-empty string for node 'node2'",
                    cannot_be_empty=True,
                    forbidden_characters=None,
                )
            ]
        )

    def test_file_not_existed_before(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location, exists=False
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            fixture_known_hosts_file_content(
                1,
                {
                    "node2": PcsKnownHost(
                        "node2", "TOKEN", [Destination("node2", 2224)]
                    )
                },
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(),
            {
                "node2": HostWithTokenAuthData(
                    "TOKEN", [Destination("node2", 2224)]
                )
            },
        )

    def test_error_reading_file(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            exception_msg="some error",
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(
                self.env_assist.get_env(),
                {
                    "node2": HostWithTokenAuthData(
                        "TOKEN", [Destination("node2", 2224)]
                    )
                },
            )
        )
        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=PCS_KNOWN_HOSTS,
                    operation="read",
                    reason="some error",
                    file_path=settings.pcsd_known_hosts_location,
                )
            ]
        )

    def test_error_reading_file_invalid_file_structure(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location, content="A"
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(
                self.env_assist.get_env(),
                {
                    "node2": HostWithTokenAuthData(
                        "TOKEN", [Destination("node2", 2224)]
                    )
                },
            )
        )
        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.PARSE_ERROR_JSON_FILE,
                    file_type_code=PCS_KNOWN_HOSTS,
                    line_number=1,
                    column_number=1,
                    position=0,
                    reason="Expecting value",
                    full_msg="Expecting value: line 1 column 1 (char 0)",
                    file_path=settings.pcsd_known_hosts_location,
                )
            ]
        )

    def test_error_writing_file(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1, _FIXTURE_KNOWN_HOSTS),
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            fixture_known_hosts_file_content(
                2,
                {
                    **_FIXTURE_KNOWN_HOSTS,
                    "node3": PcsKnownHost(
                        "node3", "TOKEN", [Destination("node3", 2224)]
                    ),
                },
            ),
            can_overwrite=True,
            exception_msg="some error",
            exception_action="",
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(
                self.env_assist.get_env(),
                {
                    "node3": HostWithTokenAuthData(
                        "TOKEN", [Destination("node3", 2224)]
                    )
                },
            )
        )

        self.env_assist.assert_reports(
            [
                fixture.error(
                    reports.codes.FILE_IO_ERROR,
                    file_type_code=PCS_KNOWN_HOSTS,
                    operation="write",
                    reason="some error",
                    file_path=settings.pcsd_known_hosts_location,
                )
            ]
        )
