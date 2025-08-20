from dataclasses import replace
from typing import Sequence

from pcs.common.host import PcsKnownHost
from pcs.lib.interface.config import SyncVersionFacadeInterface

from .types import KnownHosts


class Facade(SyncVersionFacadeInterface):
    def __init__(self, parsed_config: KnownHosts):
        super().__init__(parsed_config)

    @classmethod
    def create(
        cls,
    ) -> "Facade":
        return cls(KnownHosts(format_version=1, data_version=1, known_hosts={}))

    @property
    def config(self) -> KnownHosts:
        return self._config

    @property
    def data_version(self) -> int:
        return self.config.data_version

    def set_data_version(self, data_version: int) -> None:
        self._set_config(replace(self.config, data_version=data_version))

    @property
    def known_hosts(self) -> dict[str, PcsKnownHost]:
        return dict(self.config.known_hosts)

    def update_known_hosts(self, hosts: Sequence[PcsKnownHost]) -> None:
        updated_hosts = self.known_hosts
        for host in hosts:
            updated_hosts[host.name] = host

        self._set_config(replace(self.config, known_hosts=updated_hosts))
