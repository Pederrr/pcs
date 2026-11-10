"""
Declarative CLI test framework for testing pcs CLI commands.

Tests specify a command string and expected library calls. The run() function
drives app.main() with a mocked library wrapper and verifies exit code,
stdout/stderr, and library call sequence.

stdout/stderr default to "" and are always checked. Any command that prints or
errors must specify the exact expected text.

Example::

    from pcs_test.tools import cli_test_framework as ctf

    class TagCreate(TestCase):
        def test_minimum_args(self):
            ctf.run(
                "tag create tag1 id1 id2",
                lib_calls=[
                    ctf.LibCallSpec("tag.create", ("tag1", ["id1", "id2"])),
                ],
            )

        def test_no_args(self):
            ctf.run(
                "tag create",
                exit_code=1,
                stderr=ctf.prepare_stderr_usage("tag", "create")
            )

APIv2 commands called via lib.apiv2 are expected using ApiV2CallSpec, which
takes the APIv2 command name (as in COMMAND_MAP) and keyword arguments::

    ctf.run(
        "host deauth node1",
        lib_calls=[
            ctf.ApiV2CallSpec("auth.deauth_hosts", {"hosts": ["node1"]}),
        ],
    )
"""

from pcs_test.tools.cli_test_framework.call_specs import (
    ApiV2CallSpec,
    LibCallSpec,
)
from pcs_test.tools.cli_test_framework.runner import (
    prepare_stderr_reports,
    prepare_stderr_usage,
    run,
)
