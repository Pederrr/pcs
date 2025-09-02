import json
from typing import Mapping, Optional
from unittest import TestCase

from pcs import settings
from pcs.common import file_type_codes, reports
from pcs.common.communication.const import COM_STATUS_SUCCESS
from pcs.common.communication.dto import InternalCommunicationResultDto
from pcs.common.communication.types import CommunicationResultStatus
from pcs.common.file_type_codes import PCS_KNOWN_HOSTS
from pcs.common.host import PcsKnownHost
from pcs.common.interface.dto import to_dict
from pcs.common.node_communicator import RequestTarget
from pcs.common.pcs_cfgsync_dto import SyncConfigsDto
from pcs.lib.file.instance import FileInstance
from pcs.lib.host.config.exporter import Exporter as KnownHostsExporter
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.host.config.types import KnownHosts
from pcs.lib.pcs_cfgsync import save_sync

from pcs_test.tools.command_env import get_env_tools


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


def fixture_known_hosts_file_content(
    data_version: int = 1,
    known_hosts: Optional[Mapping[str, PcsKnownHost]] = None,
) -> str:
    return KnownHostsExporter.export(
        KnownHosts(
            format_version=1,
            data_version=data_version,
            known_hosts=known_hosts or {},
        )
    ).decode("utf-8")


class SaveSyncNewVersion(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)
        self.local_file_version = 1
        self.local_file = fixture_known_hosts_file_content(1)
        self.local_known_hosts_facade = KnownHostsFacade(
            KnownHosts(
                format_version=1,
                data_version=self.local_file_version,
                known_hosts={},
            )
        )

    def test_success_first_try(self):
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    self.local_file_version + 1
                )
            },
            node_labels=["node1", "node2"],
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_version(
            FileInstance.for_known_hosts(),
            self.local_known_hosts_facade,
            "cluster",
            [RequestTarget("node1"), RequestTarget("node2")],
            env.get_node_communicator(),
            env.report_processor,
            False,
        )
        self.assertTrue(no_conflict)

    def test_conflict_no_fetch(self):
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    self.local_file_version + 1
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {
                    "label": "node2",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "error"},
                        }
                    ),
                },
                {"label": "node3"},
            ],
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_version(
            FileInstance.for_known_hosts(),
            self.local_known_hosts_facade,
            "cluster",
            [
                RequestTarget("node1"),
                RequestTarget("node2"),
                RequestTarget("node3"),
            ],
            env.get_node_communicator(),
            env.report_processor,
            fetch_on_conflict=False,
        )
        self.assertFalse(no_conflict)
        # TODO something should be here
        self.env_assist.assert_reports([])

    def test_conflict_fetch_success(self):
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    self.local_file_version + 1
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "node2"},
            ],
        )

        # fetching the newest config from cluster
        remote_known_hosts_file = fixture_known_hosts_file_content(42)
        self.config.http.place_multinode_call(
            "fetch.get_configs",
            node_labels=["node1", "node2"],
            output=fixture_communication_result_string(
                data=SyncConfigsDto(
                    cluster_name="cluster",
                    configs={
                        file_type_codes.PCS_KNOWN_HOSTS: remote_known_hosts_file
                    },
                )
            ),
            action="api/v1/cfgsync-get-configs/v1",
            raw_data=json.dumps({"cluster_name": "cluster"}),
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=self.local_file,
        )

        # writing the received file locally
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            file_data=remote_known_hosts_file.encode("utf-8"),
            can_overwrite=True,
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_version(
            FileInstance.for_known_hosts(),
            self.local_known_hosts_facade,
            "cluster",
            [RequestTarget("node1"), RequestTarget("node2")],
            env.get_node_communicator(),
            env.report_processor,
            fetch_on_conflict=True,
        )
        self.assertFalse(no_conflict)
        # TODO something should be here
        self.env_assist.assert_reports([])

    def test_conflict_fetch_file_write_error(self):
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    self.local_file_version + 1
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "node2"},
            ],
        )

        # fetching the newest config from cluster
        remote_known_hosts_file = fixture_known_hosts_file_content(42)
        self.config.http.place_multinode_call(
            "fetch.get_configs",
            node_labels=["node1", "node2"],
            output=fixture_communication_result_string(
                data=SyncConfigsDto(
                    cluster_name="cluster",
                    configs={
                        file_type_codes.PCS_KNOWN_HOSTS: remote_known_hosts_file
                    },
                )
            ),
            action="api/v1/cfgsync-get-configs/v1",
            raw_data=json.dumps({"cluster_name": "cluster"}),
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=self.local_file,
        )

        # writing the received file locally
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            file_data=remote_known_hosts_file.encode("utf-8"),
            can_overwrite=True,
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_version(
            FileInstance.for_known_hosts(),
            self.local_known_hosts_facade,
            "cluster",
            [RequestTarget("node1"), RequestTarget("node2")],
            env.get_node_communicator(),
            env.report_processor,
            fetch_on_conflict=True,
        )
        self.assertFalse(no_conflict)
        # TODO something should be here
        self.env_assist.assert_reports([])


class SaveSyncNewKnownHosts(TestCase):
    def setUp(self):
        self.env_assist, self.config = get_env_tools(self)

    def test_success_first_try(self):
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=fixture_known_hosts_file_content(1),
        )

        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    2, {"NODE": PcsKnownHost("NODE", "TOKEN", [])}
                )
            },
            node_labels=["node1", "node2"],
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_known_hosts(
            [PcsKnownHost("NODE", "TOKEN", [])],
            "cluster",
            [
                RequestTarget("node1"),
                RequestTarget("node2"),
            ],
            env.get_node_communicator(),
            env.report_processor,
        )
        self.assertTrue(no_conflict)

    def test_conflict_success_after_merge(self):
        local_file_version = 1
        remote_file_version = 42
        local_tokens = {"LOCAL": PcsKnownHost("LOCAL", "LOCAL", [])}
        remote_tokens = {"REMOTE": PcsKnownHost("REMOTE", "REMOTE", [])}
        new_tokens = {"NEW": PcsKnownHost("NEW", "NEW", [])}

        # read local file and send it to other nodes
        local_file = fixture_known_hosts_file_content(
            local_file_version, local_tokens
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="raw_file.exists.1",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=local_file,
            name="raw_file.read.1",
        )
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    local_file_version + 1, local_tokens | new_tokens
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "node2"},
            ],
            name="set_configs.1",
        )

        # fetching the newest config from cluster
        remote_file = fixture_known_hosts_file_content(
            remote_file_version, remote_tokens
        )
        self.config.http.place_multinode_call(
            node_labels=["node1", "node2"],
            output=fixture_communication_result_string(
                data=SyncConfigsDto(
                    cluster_name="cluster",
                    configs={file_type_codes.PCS_KNOWN_HOSTS: remote_file},
                )
            ),
            action="api/v1/cfgsync-get-configs/v1",
            raw_data=json.dumps({"cluster_name": "cluster"}),
            name="get_configs.1",
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="raw_file.exists.2",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=local_file,
            name="raw_file.read.2",
        )

        # sending the merged tokens
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    remote_file_version + 1,
                    local_tokens | new_tokens | remote_tokens,
                )
            },
            node_labels=["node1", "node2"],
            name="set_configs.2",
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_known_hosts(
            list(new_tokens.values()),
            "cluster",
            [
                RequestTarget("node1"),
                RequestTarget("node2"),
            ],
            env.get_node_communicator(),
            env.report_processor,
        )
        self.assertTrue(no_conflict)

    def test_conflict_even_more_conflict(self):
        local_file_version = 1
        local_tokens = {"LOCAL": PcsKnownHost("LOCAL", "LOCAL", [])}

        remote_file_version = 42
        remote_tokens = {"REMOTE": PcsKnownHost("REMOTE", "REMOTE", [])}

        new_tokens = {"NEW": PcsKnownHost("NEW", "NEW", [])}

        even_more_new_remote_file_version = 69
        even_more_new_remote_tokens = {"WHAT": PcsKnownHost("WHAT", "WHAT", [])}

        # read local file and send it to other nodes
        local_file = fixture_known_hosts_file_content(
            local_file_version, local_tokens
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS, settings.pcsd_known_hosts_location
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=local_file,
            name="raw_file.exists.1",
        )
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    local_file_version + 1, local_tokens | new_tokens
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "node2"},
            ],
            name="set_configs.1",
        )

        # fetching the newest config from cluster
        remote_file = fixture_known_hosts_file_content(
            remote_file_version, remote_tokens
        )
        self.config.http.place_multinode_call(
            node_labels=["node1", "node2"],
            output=fixture_communication_result_string(
                data=SyncConfigsDto(
                    cluster_name="cluster",
                    configs={file_type_codes.PCS_KNOWN_HOSTS: remote_file},
                )
            ),
            action="api/v1/cfgsync-get-configs/v1",
            raw_data=json.dumps({"cluster_name": "cluster"}),
            name="get_configs.1",
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="raw_file.exists.2",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=local_file,
            name="raw_file.read.2",
        )

        # sending the merged tokens
        self.config.http.pcs_cfgsync.set_configs(
            "cluster",
            {
                PCS_KNOWN_HOSTS: fixture_known_hosts_file_content(
                    remote_file_version + 1,
                    local_tokens | new_tokens | remote_tokens,
                )
            },
            communication_list=[
                {
                    "label": "node1",
                    "output": json.dumps(
                        {
                            "status": "ok",
                            "result": {PCS_KNOWN_HOSTS: "rejected"},
                        }
                    ),
                },
                {"label": "node2"},
            ],
            name="set_configs.2",
        )

        # fetching the even more newest config from cluster
        even_more_new_remote_file = fixture_known_hosts_file_content(
            even_more_new_remote_file_version, even_more_new_remote_tokens
        )
        self.config.http.place_multinode_call(
            node_labels=["node1", "node2"],
            output=fixture_communication_result_string(
                data=SyncConfigsDto(
                    cluster_name="cluster",
                    configs={
                        file_type_codes.PCS_KNOWN_HOSTS: even_more_new_remote_file
                    },
                )
            ),
            action="api/v1/cfgsync-get-configs/v1",
            raw_data=json.dumps({"cluster_name": "cluster"}),
            name="get_configs.2",
        )
        self.config.raw_file.exists(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            name="raw_file.exists.3",
        )
        self.config.raw_file.read(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            content=local_file,
            name="raw_file.read.3",
        )

        # writing the even more new file file locally
        self.config.raw_file.write(
            PCS_KNOWN_HOSTS,
            settings.pcsd_known_hosts_location,
            file_data=even_more_new_remote_file.encode("utf-8"),
            can_overwrite=True,
        )

        env = self.env_assist.get_env()
        no_conflict = save_sync.save_sync_new_known_hosts(
            list(new_tokens.values()),
            "cluster",
            [
                RequestTarget("node1"),
                RequestTarget("node2"),
            ],
            env.get_node_communicator(),
            env.report_processor,
        )
        self.assertFalse(no_conflict)
        # TODO something should be here
        self.env_assist.assert_reports([])
