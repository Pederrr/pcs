from unittest import TestCase

from pcs.common.host import Destination, PcsKnownHost
from pcs.lib.host.config.facade import Facade as KnownHostsFacade
from pcs.lib.host.config.types import KnownHosts


class Facade(TestCase):
    _FIXTURE_FACADE = KnownHostsFacade(
        KnownHosts(
            format_version=1,
            data_version=10,
            known_hosts={
                "a": PcsKnownHost(
                    name="a",
                    token="abcd",
                    dest_list=[Destination("10.0.0.1", 2224)],
                )
            },
        )
    )

    def test_create(self):
        facade = KnownHostsFacade.create()

        self.assertEqual(1, facade.data_version)
        self.assertEqual(dict(), facade.known_hosts)

    def test_ok(self):
        facade = self._FIXTURE_FACADE

        self.assertEqual(10, facade.data_version)
        self.assertEqual(
            {
                "a": PcsKnownHost(
                    name="a",
                    token="abcd",
                    dest_list=[Destination("10.0.0.1", 2224)],
                )
            },
            facade.known_hosts,
        )

    def test_update_known_hosts_add_new_host(self):
        facade = self._FIXTURE_FACADE

        facade.update_known_hosts(
            [
                PcsKnownHost(
                    name="b",
                    token="wxyz",
                    dest_list=[Destination("10.0.0.2", 2224)],
                )
            ]
        )

        self.assertEqual(
            {
                "a": PcsKnownHost(
                    name="a",
                    token="abcd",
                    dest_list=[Destination("10.0.0.1", 2224)],
                ),
                "b": PcsKnownHost(
                    name="b",
                    token="wxyz",
                    dest_list=[Destination("10.0.0.2", 2224)],
                ),
            },
            facade.known_hosts,
        )

    def test_update_known_hosts_rewrite_existing(self):
        facade = self._FIXTURE_FACADE

        facade.update_known_hosts(
            [
                PcsKnownHost(
                    name="a",
                    token="wxyz",
                    dest_list=[Destination("10.0.0.2", 2224)],
                )
            ]
        )

        self.assertEqual(
            {
                "a": PcsKnownHost(
                    name="a",
                    token="wxyz",
                    dest_list=[Destination("10.0.0.2", 2224)],
                ),
            },
            facade.known_hosts,
        )
