from unittest import TestCase

from pcs.common import reports
from pcs.lib.corosync import config_validators

from pcs_test.tier0.lib.corosync.test_config_validators_common import (
    TotemBase,
    TransportKnetBase,
    TransportUdpBase,
)
from pcs_test.tools import fixture
from pcs_test.tools.assertions import assert_report_item_list_equal


class UpdateTotem(TotemBase, TestCase):
    def call_function(self, options):
        return config_validators.update_totem(options)

    def test_empty_values_allowed(self):
        assert_report_item_list_equal(
            self.call_function(dict.fromkeys(self.allowed_options, "")),
            [],
        )


class UpdateTransportKnet(TransportKnetBase, TestCase):
    def call_function(
        self,
        generic_options,
        compression_options,
        crypto_options,
        current_crypto_options=None,
    ):
        return config_validators.update_transport_knet(
            generic_options,
            compression_options,
            crypto_options,
            current_crypto_options=(
                {} if current_crypto_options is None else current_crypto_options
            ),
        )

    def test_no_options(self):
        # This test was originally in TransportKnetBase class as it was the
        # same for both create and update. Then the create and update cases
        # changed and started to behave differently with respect to
        # report_codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED. Once
        # that is removed and the cases no longer differ, the test should be
        # moved back to the parent class.
        assert_report_item_list_equal(
            self.call_function({}, {}, {}),
            [],
        )

    def test_invalid_options(self):
        # This test was originally in TransportKnetBase class as it was the
        # same for both create and update. Then the create and update cases
        # changed and started to behave differently with respect to
        # report_codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED. Once
        # that is removed and the cases no longer differ, the test should be
        # moved back to the parent class.
        assert_report_item_list_equal(
            self.call_function(
                {
                    "level": "5",
                    "netmtu": "1500",
                },
                {
                    "cipher": "aes256",
                    "hash": "sha256",
                },
                {
                    "ip_version": "ipv4",
                    "link_mode": "active",
                },
            ),
            [
                fixture.error(
                    reports.codes.INVALID_OPTIONS,
                    option_names=["level", "netmtu"],
                    option_type="knet transport",
                    allowed=["ip_version", "knet_pmtud_interval", "link_mode"],
                    allowed_patterns=[],
                ),
                fixture.error(
                    reports.codes.INVALID_OPTIONS,
                    option_names=["cipher", "hash"],
                    option_type="compression",
                    allowed=["level", "model", "threshold"],
                    allowed_patterns=[],
                ),
                fixture.error(
                    reports.codes.INVALID_OPTIONS,
                    option_names=["ip_version", "link_mode"],
                    option_type="crypto",
                    allowed=["cipher", "hash", "model"],
                    allowed_patterns=[],
                ),
            ],
        )

    def test_empty_values_allowed(self):
        assert_report_item_list_equal(
            self.call_function(
                {
                    "ip_version": "",
                    "knet_pmtud_interval": "",
                    "link_mode": "",
                },
                {
                    "level": "",
                    "model": "",
                    "threshold": "",
                },
                {
                    "cipher": "",
                    "hash": "",
                    "model": "",
                },
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
            ],
        )

    def test_crypto_enabled_cipher_default_hash(self):
        # This test was originally in TransportKnetBase class as it was the
        # same for both create and update. Then the create and update cases
        # changed and started to behave differently with respect to
        # report_codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED. Once
        # that is removed and the cases no longer differ, the test should be
        # moved back to the parent class.
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {
                    "cipher": "aes256",
                },
            ),
            [
                self.fixture_error_prerequisite,
            ],
        )

    def test_crypto_enabled_hash_default_cipher(self):
        # This test was originally in TransportKnetBase class as it was the
        # same for both create and update. Then the create and update cases
        # changed and started to behave differently with respect to
        # report_codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED. Once
        # that is removed and the cases no longer differ, the test should be
        # moved back to the parent class.
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {
                    "hash": "sha256",
                },
            ),
            [],
        )

    def test_crypto_config_enabled_set_to_disabled(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "none", "hash": "none"},
                {"cipher": "aes256", "hash": "sha256"},
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
            ],
        )

    def test_crypto_config_enabled_set_to_default(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "", "hash": ""},
                {"cipher": "aes256", "hash": "sha256"},
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
            ],
        )

    def test_crypto_config_enabled_default_hash(self):
        assert_report_item_list_equal(
            self.call_function(
                {}, {}, {"hash": ""}, {"cipher": "aes256", "hash": "sha256"}
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
                self.fixture_error_prerequisite,
            ],
        )

    def test_crypto_config_enabled_disabled_hash(self):
        assert_report_item_list_equal(
            self.call_function(
                {}, {}, {"hash": "none"}, {"cipher": "aes256", "hash": "sha256"}
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
                self.fixture_error_prerequisite,
            ],
        )

    def test_crypto_config_enabled_changed_hash(self):
        assert_report_item_list_equal(
            self.call_function(
                {}, {}, {"hash": "md5"}, {"cipher": "aes256", "hash": "sha256"}
            ),
            [],
        )

    def test_crypto_config_enabled_changed_cipher(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "aes128"},
                {"cipher": "aes256", "hash": "sha256"},
            ),
            [],
        )

    def test_crypto_config_hash_enabled_enable_cipher(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "aes128"},
                {"hash": "sha256"},
            ),
            [],
        )

    def test_crypto_config_hash_enabled_enable_cipher_disable_hash(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "aes128", "hash": "none"},
                {"hash": "sha256"},
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
                self.fixture_error_prerequisite,
            ],
        )

    def test_crypto_config_hash_enabled_enable_cipher_default_hash(self):
        assert_report_item_list_equal(
            self.call_function(
                {},
                {},
                {"cipher": "aes128", "hash": ""},
                {"hash": "sha256"},
            ),
            [
                fixture.deprecation(
                    reports.codes.COROSYNC_CONFIG_DISABLING_ENCRYPTION_DEPRECATED
                ),
                self.fixture_error_prerequisite,
            ],
        )


class UpdateTransportUdp(TransportUdpBase, TestCase):
    def call_function(
        self, generic_options, compression_options, crypto_options
    ):
        return config_validators.update_transport_udp(
            generic_options, compression_options, crypto_options
        )

    def test_empty_values_allowed(self):
        assert_report_item_list_equal(
            self.call_function({"ip_version": "", "netmtu": ""}, {}, {}),
            [],
        )
