from unittest import TestCase

from pcs.common.communication.utils import is_proxy_set


class IsProxySetTest(TestCase):
    def test_without_proxy(self):
        self.assertFalse(
            is_proxy_set(
                {
                    "var1": "value",
                    "var2": "val",
                }
            )
        )

    def test_multiple(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "var1": "val",
                    "https_proxy": "test.proxy",
                    "var2": "val",
                    "all_proxy": "test2.proxy",
                    "var3": "val",
                }
            )
        )

    def test_empty_string(self):
        self.assertFalse(
            is_proxy_set(
                {
                    "all_proxy": "",
                }
            )
        )

    def test_http_proxy(self):
        self.assertFalse(
            is_proxy_set(
                {
                    "http_proxy": "test.proxy",
                }
            )
        )

    def test_HTTP_PROXY(self):
        self.assertFalse(
            is_proxy_set(
                {
                    "HTTP_PROXY": "test.proxy",
                }
            )
        )

    def test_https_proxy(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "https_proxy": "test.proxy",
                }
            )
        )

    def test_HTTPS_PROXY(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "HTTPS_PROXY": "test.proxy",
                }
            )
        )

    def test_all_proxy(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "all_proxy": "test.proxy",
                }
            )
        )

    def test_ALL_PROXY(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "ALL_PROXY": "test.proxy",
                }
            )
        )

    def test_no_proxy(self):
        self.assertTrue(
            is_proxy_set(
                {
                    "no_proxy": "*",
                    "all_proxy": "test.proxy",
                }
            )
        )
