from unittest import TestCase

from pcs import settings
from pcs.common import reports
from pcs.common.file_type_codes import PCS_KNOWN_HOSTS
from pcs.common.host import Destination, PcsKnownHost
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
    )


_FIXTURE_KNOWN_HOSTS = {
    "node1": PcsKnownHost("node1", "aaa", [Destination("node1", 2224)]),
    "node2": PcsKnownHost("node2", "bbb", [Destination("node2", 2224)]),
}


class AuthHostsTokenNoSync(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def test_adds_new_host(self):
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
                    "node3": PcsKnownHost("node3", "TOKEN", []),
                },
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(), [PcsKnownHost("node3", "TOKEN", [])]
        )

    def test_rewrites_existing_host(self):
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
                    "node2": PcsKnownHost("node2", "TOKEN", []),
                },
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(), [PcsKnownHost("node2", "TOKEN", [])]
        )

    def test_invalid_known_hosts(self):
        # TODO - invalid token
        # TODO - invalid dest_list
        pass

    def test_file_not_existed_before(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location, exists=False
        )
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            fixture_known_hosts_file_content(
                1, {"node2": PcsKnownHost("node2", "TOKEN", [])}
            ),
            can_overwrite=True,
        )

        auth.auth_hosts_token_no_sync(
            self.env_assist.get_env(), [PcsKnownHost("node2", "TOKEN", [])]
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
            lambda: auth.auth_hosts_token_no_sync(self.env_assist.get_env(), [])
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
            lambda: auth.auth_hosts_token_no_sync(self.env_assist.get_env(), [])
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
                    "node3": PcsKnownHost("node3", "TOKEN", []),
                },
            ),
            can_overwrite=True,
            exception_msg="some error",
            exception_action="",
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(
                self.env_assist.get_env(), [PcsKnownHost("node3", "TOKEN", [])]
            )
        )

        self.env_assist.assert_raise_library_error(
            lambda: auth.auth_hosts_token_no_sync(self.env_assist.get_env(), [])
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
