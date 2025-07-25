# pylint: disable=too-many-lines
import datetime
import os
import unittest
from collections import namedtuple
from textwrap import dedent

from lxml import etree

from pcs import settings
from pcs.common import const
from pcs.common.str_tools import format_list
from pcs.constraint import LOCATION_NODE_VALIDATION_SKIP_MSG

from pcs_test.tools.assertions import (
    AssertPcsMixin,
    ac,
)
from pcs_test.tools.bin_mock import get_mock_settings
from pcs_test.tools.cib import get_assert_pcs_effect_mixin
from pcs_test.tools.fixture_cib import (
    CachedCibFixture,
    fixture_master_xml,
    fixture_to_cib,
    wrap_element_by_master,
    wrap_element_by_master_file,
)
from pcs_test.tools.misc import (
    ParametrizedTestMetaClass,
    get_tmp_file,
    outdent,
    skip_unless_crm_rule,
    write_file_to_tmpfile,
)
from pcs_test.tools.misc import get_test_resource as rc
from pcs_test.tools.pcs_runner import (
    PcsRunner,
    pcs,
)

LOCATION_NODE_VALIDATION_SKIP_WARNING = (
    f"Warning: {LOCATION_NODE_VALIDATION_SKIP_MSG}\n"
)
ERRORS_HAVE_OCCURRED = (
    "Error: Errors have occurred, therefore pcs is unable to continue\n"
)
WARN_WITH_RULES_SKIP = "Warning: Constraints with rules are not displayed.\n"
CRM_RULE_MISSING_MSG = (
    "crm_rule is not available, therefore expired parts of configuration may "
    "not be detected. Consider upgrading pacemaker."
)
DEPRECATED_DASH_DASH_GROUP = (
    "Deprecation Warning: Using '--group' is deprecated and will be replaced "
    "with 'group' in a future release. Specify --future to switch to the future "
    "behavior.\n"
)
DEPRECATED_LOCATION_CONSTRAINT_REMOVE = (
    "Deprecation Warning: This command is deprecated and will be removed. "
    "Please use 'pcs constraint delete' or 'pcs constraint remove' instead.\n"
)
DEPRECATED_STANDALONE_SCORE = (
    "Deprecation Warning: Specifying score as a standalone value is deprecated "
    "and might be removed in a future release, use score=value instead\n"
)

empty_cib = rc("cib-empty-3.7.xml")
large_cib = rc("cib-large.xml")


class ConstraintTestCibFixture(CachedCibFixture):
    def _setup_cib(self):
        self.assert_pcs_success(
            "resource create D1 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D2 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D3 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D4 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D5 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D6 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success("resource clone D3".split())

        # pcs no longer allows turning resources into masters but supports
        # existing ones. In order to test it, we need to put a master in the
        # CIB without pcs.
        wrap_element_by_master_file(self.cache_path, "D4", master_id="Master")


CIB_FIXTURE = ConstraintTestCibFixture("fixture_tier1_constraints", empty_cib)


@skip_unless_crm_rule()
class ConstraintTest(unittest.TestCase, AssertPcsMixin):
    # pylint: disable=too-many-public-methods
    def setUp(self):
        self.temp_cib = get_tmp_file("tier1_constraints")
        write_file_to_tmpfile(empty_cib, self.temp_cib)
        self.temp_corosync_conf = None
        self.pcs_runner = PcsRunner(self.temp_cib.name)
        self.pcs_runner.mock_settings = get_mock_settings()

    def tearDown(self):
        self.temp_cib.close()
        if self.temp_corosync_conf:
            self.temp_corosync_conf.close()

    def fixture_resources(self):
        write_file_to_tmpfile(CIB_FIXTURE.cache_path, self.temp_cib)

    def test_empty_constraints(self):
        self.assert_pcs_success(["constraint"])

    def test_multiple_order_constraints(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stop D1 then stop D2".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: first-action=stop then-action=stop)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then start D2".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            ["constraint", "--full"],
            stdout_full=outdent(
                """\
                Order Constraints:
                  stop resource 'D1' then stop resource 'D2' (id: order-D1-D2-mandatory)
                  start resource 'D1' then start resource 'D2' (id: order-D1-D2-mandatory-1)
                """
            ),
        )

    def test_order_options_empty_value(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 option1=".split(),
        )
        self.assertIn("value of 'option1' option is empty", stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

    def test_order_score(self):
        self.fixture_resources()
        self.assert_pcs_success(
            "constraint order D1 then D2 score=123".split(),
            stderr_full=(
                "Deprecation Warning: option 'score' is deprecated and might "
                "be removed in a future release, therefore it should not be used\n"
                "Adding D1 D2 (score: 123) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )

    def test_order_too_many_resources(self):
        msg = (
            "Error: Multiple 'then's cannot be specified.\n"
            "Hint: Use the 'pcs constraint order set' command if you want to "
            "create a constraint for more than two resources.\n"
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 then D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then start D2 then start D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then D2 then D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then start D2 then D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 then start D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then D2 then start D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then start D2 then start D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then D2 then start D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

    def test_order_constraint_require_all(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name, "cluster cib-upgrade".split()
        )
        self.assertEqual(
            stderr, "Cluster CIB has been upgraded to latest version\n"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then start D2 require-all=false".split(),
        )
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: require-all=false first-action=start then-action=start)\n",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Order Constraints:
                  start resource 'D1' then start resource 'D2' (id: order-D1-D2-mandatory)
                    require-all=0
                """
            ),
        )

    def test_all_constraints(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 prefers node1".split(),
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order Master then D5".split()
        )
        self.assertEqual(retval, 0)
        ac(
            stderr,
            "Adding Master D5 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(stdout, "")

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add Master with D5".split(),
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D5' prefers node 'node1' with score INFINITY (id: location-D5-node1-INFINITY)
                Colocation Constraints:
                  resource 'Master' with resource 'D5' (id: colocation-Master-D5-INFINITY)
                    score=INFINITY
                Order Constraints:
                  start resource 'Master' then start resource 'D5' (id: order-Master-D5-mandatory)
                """
            ),
        )

        self.assert_pcs_success(
            "constraint config --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D5' prefers node 'node1' with score INFINITY (id: location-D5-node1-INFINITY)
                Colocation Constraints:
                  resource 'Master' with resource 'D5' (id: colocation-Master-D5-INFINITY)
                    score=INFINITY
                Order Constraints:
                  start resource 'Master' then start resource 'D5' (id: order-Master-D5-mandatory)
                """
            ),
        )

    # see also BundleLocation
    def test_location_constraints(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 prefers node1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 avoids node1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 prefers node1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 avoids node2".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location add location-D5-node1-INFINITY".split(),
        )
        self.assertEqual(retval, 1)
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(stdout, "")

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D5' prefers node 'node1' with score INFINITY (id: location-D5-node1-INFINITY)
                  resource 'D5' avoids node 'node2' with score INFINITY (id: location-D5-node2--INFINITY)
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location delete location-D5-node1-INFINITY".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_LOCATION_CONSTRAINT_REMOVE)
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location remove location-D5-node2--INFINITY".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_LOCATION_CONSTRAINT_REMOVE)
        self.assertEqual(retval, 0)

        self.assert_pcs_success("constraint --full".split())

    def test_constraint_removal(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D5 prefers node1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D6 prefers node1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint remove blahblah".split()
        )
        self.assertEqual(retval, 1)
        self.assertTrue(
            stderr.startswith(
                "Error: Unable to find constraints or constraint rules: "
                "'blahblah'"
            ),
            stderr,
        )
        self.assertEqual(stdout, "")

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint delete blahblah".split()
        )
        self.assertEqual(retval, 1)
        self.assertTrue(
            stderr.startswith(
                "Error: Unable to find constraints or constraint rules: "
                "'blahblah'"
            ),
            stderr,
        )
        self.assertEqual(stdout, "")

        self.assert_pcs_success(
            "constraint location config --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D5' prefers node 'node1' with score INFINITY (id: location-D5-node1-INFINITY)
                  resource 'D6' prefers node 'node1' with score INFINITY (id: location-D6-node1-INFINITY)
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint remove location-D5-node1-INFINITY location-D6-node1-INFINITY".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success("constraint location config --full".split())

    # see also BundleColocation
    def test_colocation_constraints(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        self.fixture_resources()
        # pcs no longer allows creating masters but supports existing ones. In
        # order to test it, we need to put a master in the CIB without pcs.
        fixture_to_cib(
            self.temp_cib.name,
            "\n".join(
                ["<resources>"]
                + [fixture_master_xml(f"M{i}") for i in range(1, 11)]
                + ["</resources>"]
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D3-clone".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 100".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_STANDALONE_SCORE)
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "--force -- constraint colocation add D1 with D2 -100".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_STANDALONE_SCORE)
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add Master with D5 score=100".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            f"constraint colocation add {const.PCMK_ROLE_PROMOTED} M1-master with {const.PCMK_ROLE_PROMOTED} M2-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add M3-master with M4-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        role = str(const.PCMK_ROLE_UNPROMOTED_LEGACY).lower()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            f"constraint colocation add {role} M5-master with started M6-master 500".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid role value '{}', allowed values are: {}\n".format(
                role, format_list(const.PCMK_ROLES)
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            f"constraint colocation add {const.PCMK_ROLE_UNPROMOTED} M5-master with started M6-master score=500".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            f"constraint colocation add M7-master with {const.PCMK_ROLE_PROMOTED} M8-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            f"constraint colocation add {const.PCMK_ROLE_UNPROMOTED} M9-master with M10-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            ["constraint"],
            stdout_full=outdent(
                f"""\
                Colocation Constraints:
                  resource 'D1' with resource 'D3-clone'
                    score=INFINITY
                  resource 'D1' with resource 'D2'
                    score=100
                  resource 'D1' with resource 'D2'
                    score=-100
                  resource 'Master' with resource 'D5'
                    score=100
                  {const.PCMK_ROLE_PROMOTED} resource 'M1-master' with {const.PCMK_ROLE_PROMOTED} resource 'M2-master'
                    score=INFINITY
                  resource 'M3-master' with resource 'M4-master'
                    score=INFINITY
                  {const.PCMK_ROLE_UNPROMOTED} resource 'M5-master' with Started resource 'M6-master'
                    score=500
                  Started resource 'M7-master' with {const.PCMK_ROLE_PROMOTED} resource 'M8-master'
                    score=INFINITY
                  {const.PCMK_ROLE_UNPROMOTED} resource 'M9-master' with Started resource 'M10-master'
                    score=INFINITY
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation delete M1-master M2-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation remove M5-master M6-master".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            ["constraint"],
            stdout_full=outdent(
                f"""\
                Colocation Constraints:
                  resource 'D1' with resource 'D3-clone'
                    score=INFINITY
                  resource 'D1' with resource 'D2'
                    score=100
                  resource 'D1' with resource 'D2'
                    score=-100
                  resource 'Master' with resource 'D5'
                    score=100
                  resource 'M3-master' with resource 'M4-master'
                    score=INFINITY
                  Started resource 'M7-master' with {const.PCMK_ROLE_PROMOTED} resource 'M8-master'
                    score=INFINITY
                  {const.PCMK_ROLE_UNPROMOTED} resource 'M9-master' with Started resource 'M10-master'
                    score=INFINITY
                """
            ),
        )

    def test_colocation_syntax_errors(self):
        def assert_usage(command):
            stdout, stderr, retval = pcs(self.temp_cib.name, command)
            self.assertTrue(
                stderr.startswith(
                    "\nUsage: pcs constraint [constraints]...\n    colocation add"
                ),
                stderr,
            )
            self.assertEqual(stdout, "")
            self.assertEqual(retval, 1)

        role = str(const.PCMK_ROLE_PROMOTED).lower()

        assert_usage("constraint colocation add D1".split())
        assert_usage(f"constraint colocation add {role} D1".split())
        assert_usage("constraint colocation add D1 with".split())
        assert_usage(f"constraint colocation add {role} D1 with".split())

        assert_usage("constraint colocation add D1 D2".split())
        assert_usage(f"constraint colocation add {role} D1 D2".split())
        assert_usage(f"constraint colocation add D1 {role} D2".split())
        assert_usage(f"constraint colocation add {role} D1 {role} D2".split())

        assert_usage("constraint colocation add D1 D2 D3".split())

        self.assert_pcs_success(["constraint"])

    def test_colocation_errors(self):
        self.fixture_resources()

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D20".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Error: Resource 'D20' does not exist\n")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D10 with D20".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Error: Resource 'D10' does not exist\n")
        self.assertEqual(retval, 1)

        self.assert_pcs_success(["constraint"])

    def test_colocation_with_score_and_options(self):
        self.fixture_resources()

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "-- constraint colocation add D1 with D2 -100 id=abcd node-attribute=y".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_STANDALONE_SCORE)
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "-- constraint colocation add D2 with D1 score=-100 id=efgh node-attribute=y".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            ["constraint"],
            stdout_full=outdent(
                """\
                Colocation Constraints:
                  resource 'D1' with resource 'D2'
                    score=-100 node-attribute=y
                  resource 'D2' with resource 'D1'
                    score=-100 node-attribute=y
                """
            ),
        )

    def test_colocation_invalid_role(self):
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add abc D1 with D2".split(),
        )
        ac(
            stderr,
            "Error: invalid role value 'abc', allowed values are: {}\n".format(
                format_list(const.PCMK_ROLES)
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with def D2".split(),
        )
        ac(
            stderr,
            "Error: invalid role value 'def', allowed values are: {}\n".format(
                format_list(const.PCMK_ROLES)
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add abc D1 with def D2".split(),
        )
        ac(
            stderr,
            "Error: invalid role value 'abc', allowed values are: {}\n".format(
                format_list(const.PCMK_ROLES)
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

    def test_colocation_too_many_resources(self):
        msg = (
            "Error: Multiple 'with's cannot be specified.\n"
            "Hint: Use the 'pcs constraint colocation set' command if you want "
            "to create a constraint for more than two resources.\n"
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 with D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add promoted D1 with D2 with D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with promoted D2 with D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 with promoted D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add promoted D1 with promoted D2 with D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add promoted D1 with D2 with promoted D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with promoted D2 with promoted D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add promoted D1 with promoted D2 with promoted D3".split(),
        )
        self.assertIn(msg, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

    def test_colocation_options_empty_value(self):
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 option1=".split(),
        )
        self.assertEqual(stdout, "")
        self.assertIn("value of 'option1' option is empty", stderr)
        self.assertEqual(retval, 1)

    # see also BundleColocation
    def test_colocation_sets(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        self.fixture_resources()
        self.assert_pcs_success(
            "resource create D7 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D8 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D9 ocf:pcsmock:minimal".split()
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint colocation set".split()
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D7 D8 set".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D7 D8 set set D8 D9".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set setoptions score=100".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            (
                "constraint colocation "
                "set D5 D6 D7 sequential=false require-all=true "
                "set D8 D9 sequential=true require-all=false action=start role=Stopped "
                "setoptions score=INFINITY"
            ).split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint colocation set D5 D6".split()
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            (
                "constraint colocation "
                f"set D5 D6 action=stop role=Started set D7 D8 action=promote role={const.PCMK_ROLE_UNPROMOTED} "
                f"set D8 D9 action=demote role={const.PCMK_ROLE_PROMOTED}"
            ).split(),
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        self.assert_pcs_success(
            "constraint colocation --full".split(),
            stdout_full=outdent(
                f"""\
                Colocation Set Constraints:
                  Set Constraint: colocation_set_D5D6D7
                    score=INFINITY
                    Resource Set: colocation_set_D5D6D7_set
                      Resources: 'D5', 'D6', 'D7'
                      sequential=0 require-all=1
                    Resource Set: colocation_set_D5D6D7_set-1
                      Resources: 'D8', 'D9'
                      sequential=1 require-all=0 action=start role=Stopped
                  Set Constraint: colocation_set_D5D6
                    score=INFINITY
                    Resource Set: colocation_set_D5D6_set
                      Resources: 'D5', 'D6'
                  Set Constraint: colocation_set_D5D6D7-1
                    score=INFINITY
                    Resource Set: colocation_set_D5D6D7-1_set
                      Resources: 'D5', 'D6'
                      action=stop role=Started
                    Resource Set: colocation_set_D5D6D7-1_set-1
                      Resources: 'D7', 'D8'
                      action=promote role={const.PCMK_ROLE_UNPROMOTED}
                    Resource Set: colocation_set_D5D6D7-1_set-2
                      Resources: 'D8', 'D9'
                      action=demote role={const.PCMK_ROLE_PROMOTED}
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint delete colocation_set_D5D6".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint colocation --full".split(),
            stdout_full=outdent(
                f"""\
                Colocation Set Constraints:
                  Set Constraint: colocation_set_D5D6D7
                    score=INFINITY
                    Resource Set: colocation_set_D5D6D7_set
                      Resources: 'D5', 'D6', 'D7'
                      sequential=0 require-all=1
                    Resource Set: colocation_set_D5D6D7_set-1
                      Resources: 'D8', 'D9'
                      sequential=1 require-all=0 action=start role=Stopped
                  Set Constraint: colocation_set_D5D6D7-1
                    score=INFINITY
                    Resource Set: colocation_set_D5D6D7-1_set
                      Resources: 'D5', 'D6'
                      action=stop role=Started
                    Resource Set: colocation_set_D5D6D7-1_set-1
                      Resources: 'D7', 'D8'
                      action=promote role={const.PCMK_ROLE_UNPROMOTED}
                    Resource Set: colocation_set_D5D6D7-1_set-2
                      Resources: 'D8', 'D9'
                      action=demote role={const.PCMK_ROLE_PROMOTED}
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "resource delete D5".split()
        )
        ac(
            stderr,
            outdent(
                """\
            Removing references:
              Resource 'D5' from:
                Resource sets: 'colocation_set_D5D6D7-1_set', 'colocation_set_D5D6D7_set'
            """
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "resource delete D6".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            outdent(
                """\
            Removing dependant element:
              Resource set: 'colocation_set_D5D6D7-1_set'
            Removing references:
              Resource 'D6' from:
                Resource set: 'colocation_set_D5D6D7_set'
              Resource set 'colocation_set_D5D6D7-1_set' from:
                Colocation constraint: 'colocation_set_D5D6D7-1'
            """
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint ref D7".split()
        )
        ac(
            stdout,
            outdent(
                """\
            Resource: D7
              colocation_set_D5D6D7
              colocation_set_D5D6D7-1
            """
            ),
        )
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint ref D8".split()
        )
        ac(
            stdout,
            outdent(
                """\
            Resource: D8
              colocation_set_D5D6D7
              colocation_set_D5D6D7-1
            """
            ),
        )
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 sequential=foo".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid sequential value, use a pacemaker "
                "boolean value: '0', '1', 'false', 'n', 'no', 'off', 'on', "
                "'true', 'y', 'yes'\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 require-all=foo".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid require-all value, use a "
                "pacemaker boolean value: '0', '1', 'false', 'n', 'no', "
                "'off', 'on', 'true', 'y', 'yes'\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 role=foo".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid role value, use {}\n"
                + ERRORS_HAVE_OCCURRED
            ).format(format_list(const.PCMK_ROLES)),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 action=foo".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid action value, use 'demote', 'promote', 'start', 'stop'\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 foo=bar".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: invalid set option 'foo', allowed options are: 'action', 'require-all', 'role', 'sequential'\n"
                "Error: Errors have occurred, therefore pcs is unable to continue\n"
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 setoptions foo=bar".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid option 'foo', allowed options are: 'id', 'score'\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 setoptions score=foo".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid score 'foo', use integer or INFINITY or -INFINITY\n",
        )
        self.assertEqual(retval, 1)

    def test_constraint_resource_discovery(self):
        self.assert_pcs_success(
            "resource create crd1 ocf:pcsmock:minimal".split(),
        )
        self.assert_pcs_success(
            "resource create crd2 ocf:pcsmock:minimal".split(),
        )
        self.assert_pcs_success(
            "resource create crd3 ocf:pcsmock:minimal".split(),
        )
        self.assert_pcs_success(
            "resource create crd4 ocf:pcsmock:minimal".split(),
        )

        self.assert_pcs_success(
            "-- constraint location add id1 crd1 my_node -INFINITY resource-discovery=always".split(),
            stderr_full=(
                DEPRECATED_STANDALONE_SCORE
                + LOCATION_NODE_VALIDATION_SKIP_WARNING
            ),
        )
        self.assert_pcs_success(
            "-- constraint location add id2 crd2 my_node score=-INFINITY resource-discovery=never".split(),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )
        self.assert_pcs_success(
            "-- constraint location add id3 crd3 my_node resource-discovery=always score=10".split(),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )
        self.assert_pcs_success(
            "-- constraint location add id4 crd4 my_node resource-discovery=never".split(),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )
        self.assert_pcs_fail(
            "-- constraint location add id5 crd1 my_node2 resource-discovery=never INFINITY".split(),
            "Error: missing value of 'INFINITY' option\n",
        )
        self.assert_pcs_fail(
            "-- constraint location add id6 crd1 my_node2 -INFINITY bad-opt=test".split(),
            (
                DEPRECATED_STANDALONE_SCORE
                + "Error: bad option 'bad-opt', use --force to override\n"
            ),
        )
        self.assert_pcs_fail(
            "-- constraint location add id7 crd1 my_node2 score=-INFINITY bad-opt=test".split(),
            "Error: bad option 'bad-opt', use --force to override\n",
        )

        self.assert_pcs_fail(
            "-- constraint location add id7 crd1 my_node2 score=-INFINITY resource-discovery=bad-value".split(),
            "Error: invalid resource-discovery value 'bad-value', allowed values "
            "are: 'always', 'exclusive', 'never', use --force to override\n",
        )

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'crd1' avoids node 'my_node' with score INFINITY (id: id1)
                    resource-discovery=always
                  resource 'crd2' avoids node 'my_node' with score INFINITY (id: id2)
                    resource-discovery=never
                  resource 'crd3' prefers node 'my_node' with score 10 (id: id3)
                    resource-discovery=always
                  resource 'crd4' prefers node 'my_node' with score INFINITY (id: id4)
                    resource-discovery=never
                """
            ),
        )

    def test_order_sets_removal(self):
        for i in range(9):
            self.assert_pcs_success(
                f"resource create T{i} ocf:pcsmock:minimal".split(),
            )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set T0 T1 T2".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)
        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set T2 T3".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order remove T1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order remove T1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr, "Error: No matching resources found in ordering list\n"
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order delete T1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr, "Error: No matching resources found in ordering list\n"
        )
        self.assertEqual(retval, 1)

        self.assert_pcs_success(
            "constraint order".split(),
            stdout_full=outdent(
                """\
                Order Set Constraints:
                  Set Constraint:
                    Resource Set:
                      Resources: 'T0', 'T2'
                  Set Constraint:
                    Resource Set:
                      Resources: 'T2', 'T3'
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order delete T2".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint order".split(),
            stdout_full=outdent(
                """\
                Order Set Constraints:
                  Set Constraint:
                    Resource Set:
                      Resources: 'T0'
                  Set Constraint:
                    Resource Set:
                      Resources: 'T3'
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order delete T0".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint order".split(),
            stdout_full=outdent(
                """\
                Order Set Constraints:
                  Set Constraint:
                    Resource Set:
                      Resources: 'T3'
                """
            ),
        )
        # ac(stdout, "Ordering Constraints:\n  Resource Sets:\n    set T3\n")

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order remove T3".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success("constraint order".split())

    # see also BundleOrder
    def test_order_sets(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        self.fixture_resources()
        self.assert_pcs_success(
            "resource create D7 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D8 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D9 ocf:pcsmock:minimal".split()
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set".split()
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D7 D8 set".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D7 D8 set set D8 D9".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set setoptions score=100".split(),
        )
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("\nUsage: pcs constraint"), stderr)
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            (
                "constraint order "
                "set D5 D6 D7 sequential=false require-all=true "
                "set D8 D9 sequential=true require-all=false action=start role=Stopped"
            ).split(),
        )
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set D5 D6".split()
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            (
                "constraint order "
                "set D5 D6 action=stop role=Started "
                f"set D7 D8 action=promote role={const.PCMK_ROLE_UNPROMOTED_LEGACY} "
                f"set D8 D9 action=demote role={const.PCMK_ROLE_PROMOTED_LEGACY}"
            ).split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Error: '{}' is not a valid role value, use {}\n"
                + ERRORS_HAVE_OCCURRED
            ).format(
                const.PCMK_ROLE_UNPROMOTED_LEGACY,
                format_list(const.PCMK_ROLES),
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            (
                "constraint order "
                "set D5 D6 action=stop role=Started "
                f"set D7 D8 action=promote role={const.PCMK_ROLE_UNPROMOTED} "
                f"set D8 D9 action=demote role={const.PCMK_ROLE_PROMOTED}"
            ).split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint order --full".split(),
            stdout_full=outdent(
                f"""\
                Order Set Constraints:
                  Set Constraint: order_set_D5D6D7
                    Resource Set: order_set_D5D6D7_set
                      Resources: 'D5', 'D6', 'D7'
                      sequential=0 require-all=1
                    Resource Set: order_set_D5D6D7_set-1
                      Resources: 'D8', 'D9'
                      sequential=1 require-all=0 action=start role=Stopped
                  Set Constraint: order_set_D5D6
                    Resource Set: order_set_D5D6_set
                      Resources: 'D5', 'D6'
                  Set Constraint: order_set_D5D6D7-1
                    Resource Set: order_set_D5D6D7-1_set
                      Resources: 'D5', 'D6'
                      action=stop role=Started
                    Resource Set: order_set_D5D6D7-1_set-1
                      Resources: 'D7', 'D8'
                      action=promote role={const.PCMK_ROLE_UNPROMOTED}
                    Resource Set: order_set_D5D6D7-1_set-2
                      Resources: 'D8', 'D9'
                      action=demote role={const.PCMK_ROLE_PROMOTED}
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint remove order_set_D5D6".split()
        )
        self.assertEqual(retval, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        self.assert_pcs_success(
            "constraint order --full".split(),
            stdout_full=outdent(
                f"""\
                Order Set Constraints:
                  Set Constraint: order_set_D5D6D7
                    Resource Set: order_set_D5D6D7_set
                      Resources: 'D5', 'D6', 'D7'
                      sequential=0 require-all=1
                    Resource Set: order_set_D5D6D7_set-1
                      Resources: 'D8', 'D9'
                      sequential=1 require-all=0 action=start role=Stopped
                  Set Constraint: order_set_D5D6D7-1
                    Resource Set: order_set_D5D6D7-1_set
                      Resources: 'D5', 'D6'
                      action=stop role=Started
                    Resource Set: order_set_D5D6D7-1_set-1
                      Resources: 'D7', 'D8'
                      action=promote role={const.PCMK_ROLE_UNPROMOTED}
                    Resource Set: order_set_D5D6D7-1_set-2
                      Resources: 'D8', 'D9'
                      action=demote role={const.PCMK_ROLE_PROMOTED}
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "resource delete D5".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            outdent(
                """\
            Removing references:
              Resource 'D5' from:
                Resource sets: 'order_set_D5D6D7-1_set', 'order_set_D5D6D7_set'
            """
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "resource delete D6".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            outdent(
                """\
            Removing dependant element:
              Resource set: 'order_set_D5D6D7-1_set'
            Removing references:
              Resource 'D6' from:
                Resource set: 'order_set_D5D6D7_set'
              Resource set 'order_set_D5D6D7-1_set' from:
                Order constraint: 'order_set_D5D6D7-1'
            """
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 sequential=foo".split(),
        )
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid sequential value, use a pacemaker "
                "boolean value: '0', '1', 'false', 'n', 'no', 'off', 'on', "
                "'true', 'y', 'yes'\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 require-all=foo".split(),
        )
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid require-all value, use a "
                "pacemaker boolean value: '0', '1', 'false', 'n', 'no', "
                "'off', 'on', 'true', 'y', 'yes'\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 role=foo".split(),
        )
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid role value, use {}\n"
                + ERRORS_HAVE_OCCURRED
            ).format(format_list(const.PCMK_ROLES)),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 action=foo".split(),
        )
        ac(
            stderr,
            (
                "Error: 'foo' is not a valid action value, use 'demote', 'promote', 'start', 'stop'\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 foo=bar".split(),
        )
        ac(
            stderr,
            (
                "Error: invalid set option 'foo', allowed options are: 'action', 'require-all', 'role', 'sequential'\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions foo=bar".split(),
        )
        ac(
            stderr,
            """\
Error: invalid option 'foo', allowed options are: 'id', 'kind', 'symmetrical'
""",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions kind=foo".split(),
        )
        ac(
            stderr,
            "Error: 'foo' is not a valid kind value, use 'Mandatory', 'Optional', 'Serialize'\n",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions symmetrical=foo".split(),
        )
        ac(
            stderr,
            "Error: 'foo' is not a valid symmetrical value, use '0', '1', 'false', 'n', 'no', 'off', 'on', 'true', 'y', 'yes'\n",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions symmetrical=false kind=mandatory".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                f"""\
                Order Set Constraints:
                  Set Constraint: order_set_D5D6D7
                    Resource Set: order_set_D5D6D7_set
                      Resources: 'D7'
                      sequential=0 require-all=1
                    Resource Set: order_set_D5D6D7_set-1
                      Resources: 'D8', 'D9'
                      sequential=1 require-all=0 action=start role=Stopped
                  Set Constraint: order_set_D5D6D7-1
                    Resource Set: order_set_D5D6D7-1_set-1
                      Resources: 'D7', 'D8'
                      action=promote role={const.PCMK_ROLE_UNPROMOTED}
                    Resource Set: order_set_D5D6D7-1_set-2
                      Resources: 'D8', 'D9'
                      action=demote role={const.PCMK_ROLE_PROMOTED}
                  Set Constraint: order_set_D1D2
                    symmetrical=0 kind=Mandatory
                    Resource Set: order_set_D1D2_set
                      Resources: 'D1', 'D2'
                """
            ),
        )

    def test_master_slave_constraint(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        os.system(
            "CIB_file="
            + self.temp_cib.name
            + f' {settings.cibadmin_exec} -R --scope nodes --xml-text \'<nodes><node id="1" uname="rh7-1"/><node id="2" uname="rh7-2"/></nodes>\''
        )

        self.assert_pcs_success(
            "resource create dummy1 ocf:pcsmock:minimal".split(),
        )

        # pcs no longer allows creating masters but supports existing ones. In
        # order to test it, we need to put a master in the CIB without pcs.
        fixture_to_cib(self.temp_cib.name, fixture_master_xml("stateful1"))

        self.assert_pcs_success(
            "resource create stateful2 ocf:pcsmock:stateful --group statefulG".split(),
            stderr_full=DEPRECATED_DASH_DASH_GROUP,
        )

        # pcs no longer allows turning resources into masters but supports
        # existing ones. In order to test it, we need to put a master in the
        # CIB without pcs.
        wrap_element_by_master(self.temp_cib, "statefulG")

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location stateful1 prefers rh7-1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            LOCATION_NODE_VALIDATION_SKIP_WARNING
            + "Error: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location statefulG prefers rh7-1".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            LOCATION_NODE_VALIDATION_SKIP_WARNING
            + "Error: statefulG is a clone resource, you should use the clone id: statefulG-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stateful1 then dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order dummy1 then statefulG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: statefulG is a clone resource, you should use the clone id: statefulG-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set stateful1 dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set dummy1 statefulG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: statefulG is a clone resource, you should use the clone id: statefulG-master when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add stateful1 with dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add dummy1 with statefulG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: statefulG is a clone resource, you should use the clone id: statefulG-master when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set dummy1 stateful1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set statefulG dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: statefulG is a clone resource, you should use the clone id: statefulG-master when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        self.assert_pcs_success("constraint --full".split())

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location stateful1 prefers rh7-1 --force".split(),
        )
        ac(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stateful1 then dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding stateful1 dummy1 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set stateful1 dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Warning: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add stateful1 with dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set stateful1 dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Warning: stateful1 is a clone resource, you should use the clone id: stateful1-master when adding constraints\n",
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'stateful1' prefers node 'rh7-1' with score INFINITY (id: location-stateful1-rh7-1-INFINITY)
                Colocation Constraints:
                  resource 'stateful1' with resource 'dummy1' (id: colocation-stateful1-dummy1-INFINITY)
                    score=INFINITY
                Colocation Set Constraints:
                  Set Constraint: colocation_set_s1d1
                    score=INFINITY
                    Resource Set: colocation_set_s1d1_set
                      Resources: 'dummy1', 'stateful1'
                Order Constraints:
                  start resource 'stateful1' then start resource 'dummy1' (id: order-stateful1-dummy1-mandatory)
                Order Set Constraints:
                  Set Constraint: order_set_s1d1
                    Resource Set: order_set_s1d1_set
                      Resources: 'dummy1', 'stateful1'
                """
            ),
        )

    def test_clone_constraint(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        os.system(
            "CIB_file="
            + self.temp_cib.name
            + f' {settings.cibadmin_exec} -R --scope nodes --xml-text \'<nodes><node id="1" uname="rh7-1"/><node id="2" uname="rh7-2"/></nodes>\''
        )

        self.assert_pcs_success(
            "resource create dummy1 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create dummy ocf:pcsmock:minimal clone".split()
        )
        self.assert_pcs_success(
            "resource create dummy2 ocf:pcsmock:minimal --group dummyG".split(),
            stderr_full=DEPRECATED_DASH_DASH_GROUP,
        )
        self.assert_pcs_success("resource clone dummyG".split())

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location dummy prefers rh7-1".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            LOCATION_NODE_VALIDATION_SKIP_WARNING
            + "Error: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location dummyG prefers rh7-1".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            LOCATION_NODE_VALIDATION_SKIP_WARNING
            + "Error: dummyG is a clone resource, you should use the clone id: dummyG-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order dummy then dummy1".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "Error: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order dummy1 then dummyG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummyG is a clone resource, you should use the clone id: dummyG-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set dummy1 dummy".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set dummyG dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummyG is a clone resource, you should use the clone id: dummyG-clone when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add dummy with dummy1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add dummy1 with dummyG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummyG is a clone resource, you should use the clone id: dummyG-clone when adding constraints. Use --force to override.\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set dummy1 dummy".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set dummy1 dummyG".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: dummyG is a clone resource, you should use the clone id: dummyG-clone when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )
        self.assertEqual(retval, 1)

        self.assert_pcs_success("constraint --full".split())

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location dummy prefers rh7-1 --force".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order dummy then dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding dummy dummy1 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set dummy1 dummy --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Warning: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add dummy with dummy1 --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set dummy1 dummy --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "Warning: dummy is a clone resource, you should use the clone id: dummy-clone when adding constraints\n",
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'dummy' prefers node 'rh7-1' with score INFINITY (id: location-dummy-rh7-1-INFINITY)
                Colocation Constraints:
                  resource 'dummy' with resource 'dummy1' (id: colocation-dummy-dummy1-INFINITY)
                    score=INFINITY
                Colocation Set Constraints:
                  Set Constraint: colocation_set_d1dy
                    score=INFINITY
                    Resource Set: colocation_set_d1dy_set
                      Resources: 'dummy', 'dummy1'
                Order Constraints:
                  start resource 'dummy' then start resource 'dummy1' (id: order-dummy-dummy1-mandatory)
                Order Set Constraints:
                  Set Constraint: order_set_d1dy
                    Resource Set: order_set_d1dy_set
                      Resources: 'dummy', 'dummy1'
                """
            ),
        )

    def test_missing_role(self):
        os.system(
            "CIB_file="
            + self.temp_cib.name
            + f' {settings.cibadmin_exec} -R --scope nodes --xml-text \'<nodes><node id="1" uname="rh7-1"/><node id="2" uname="rh7-2"/></nodes>\''
        )

        # pcs no longer allows creating masters but supports existing ones. In
        # order to test it, we need to put a master in the CIB without pcs.
        fixture_to_cib(self.temp_cib.name, fixture_master_xml("stateful0"))

        os.system(
            "CIB_file="
            + self.temp_cib.name
            + f' {settings.cibadmin_exec} -R --scope constraints --xml-text \'<constraints><rsc_location id="cli-prefer-stateful0-master" role="Master" rsc="stateful0-master" node="rh7-1" score="INFINITY"/><rsc_location id="cli-ban-stateful0-master-on-rh7-1" rsc="stateful0-master" role="Slave" node="rh7-1" score="-INFINITY"/></constraints>\''
        )

        self.assert_pcs_success(
            ["constraint"],
            stdout_full=outdent(
                f"""\
                Location Constraints:
                  {const.PCMK_ROLE_PROMOTED} resource 'stateful0-master' prefers node 'rh7-1' with score INFINITY
                  {const.PCMK_ROLE_UNPROMOTED} resource 'stateful0-master' avoids node 'rh7-1' with score INFINITY
                """
            ),
        )

    def test_many_constraints(self):
        write_file_to_tmpfile(large_cib, self.temp_cib)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location dummy prefers rh7-1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint location config resources dummy --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  Resource: dummy
                    Prefers:
                      node 'rh7-1' with score INFINITY (id: location-dummy-rh7-1-INFINITY)
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location remove location-dummy-rh7-1-INFINITY".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, DEPRECATED_LOCATION_CONSTRAINT_REMOVE)
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add dummy1 with dummy2".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation remove dummy1 dummy2".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order dummy1 then dummy2".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding dummy1 dummy2 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order remove dummy1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location dummy prefers rh7-1".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint location config resources dummy --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  Resource: dummy
                    Prefers:
                      node 'rh7-1' with score INFINITY (id: location-dummy-rh7-1-INFINITY)
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint remove location-dummy-rh7-1-INFINITY".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

    def test_constraint_resource_clone_update(self):
        self.fixture_resources()
        self.assert_pcs_success(
            "constraint location D1 prefers rh7-1".split(),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )
        self.assert_pcs_success(
            "constraint colocation add D1 with D5".split(),
        )
        self.assert_pcs_success(
            "constraint order D1 then D5".split(),
            stderr_full="Adding D1 D5 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assert_pcs_success(
            "constraint order D6 then D1".split(),
            stderr_full="Adding D6 D1 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assert_pcs_success("resource clone D1".split())
        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D1-clone' prefers node 'rh7-1' with score INFINITY (id: location-D1-rh7-1-INFINITY)
                Colocation Constraints:
                  resource 'D1-clone' with resource 'D5' (id: colocation-D1-D5-INFINITY)
                    score=INFINITY
                Order Constraints:
                  start resource 'D1-clone' then start resource 'D5' (id: order-D1-D5-mandatory)
                  start resource 'D6' then start resource 'D1-clone' (id: order-D6-D1-mandatory)
                """
            ),
        )

    def test_constraint_group_clone_update(self):
        self.fixture_resources()
        self.assert_pcs_success("resource group add DG D1".split())
        self.assert_pcs_success(
            "constraint location DG prefers rh7-1".split(),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )
        self.assert_pcs_success(
            "constraint colocation add DG with D5".split(),
        )
        self.assert_pcs_success(
            "constraint order DG then D5".split(),
            stderr_full="Adding DG D5 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assert_pcs_success(
            "constraint order D6 then DG".split(),
            stderr_full="Adding D6 DG (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assert_pcs_success("resource clone DG".split())
        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'DG-clone' prefers node 'rh7-1' with score INFINITY (id: location-DG-rh7-1-INFINITY)
                Colocation Constraints:
                  resource 'DG-clone' with resource 'D5' (id: colocation-DG-D5-INFINITY)
                    score=INFINITY
                Order Constraints:
                  start resource 'DG-clone' then start resource 'D5' (id: order-DG-D5-mandatory)
                  start resource 'D6' then start resource 'DG-clone' (id: order-D6-DG-mandatory)
                """
            ),
        )

    def test_guest_node_constraints_remove(self):
        # pylint: disable=too-many-statements
        self.temp_corosync_conf = get_tmp_file("tier1_test_constraints")
        write_file_to_tmpfile(rc("corosync.conf"), self.temp_corosync_conf)
        self.fixture_resources()
        # constraints referencing the remote node's name,
        # deleting the remote node resource
        self.assert_pcs_success(
            (
                "resource create vm-guest1 ocf:pcsmock:minimal "
                "meta remote-node=guest1 --force"
            ).split(),
            stderr_full=(
                "Warning: this command is not sufficient for creating a guest "
                "node, use 'pcs cluster node add-guest'\n"
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D1 prefers node1=100".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D1 prefers guest1=200".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D2 avoids node2=300".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint location D2 avoids guest1=400".split(),
        )
        self.assertEqual(stderr, LOCATION_NODE_VALIDATION_SKIP_WARNING)
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D1' prefers node 'node1' with score 100 (id: location-D1-node1-100)
                  resource 'D1' prefers node 'guest1' with score 200 (id: location-D1-guest1-200)
                  resource 'D2' avoids node 'node2' with score 300 (id: location-D2-node2--300)
                  resource 'D2' avoids node 'guest1' with score 400 (id: location-D2-guest1--400)
                """
            ),
        )

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "cluster node remove-guest guest1".split(),
            corosync_conf_opt=self.temp_corosync_conf.name,
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            outdent(
                """\
            Running action(s) 'pacemaker_remote disable', 'pacemaker_remote stop' on 'guest1' was skipped because the command does not run on a live cluster (e.g. -f was used). Please, run the action(s) manually.
            Removing 'pacemaker authkey' from 'guest1' was skipped because the command does not run on a live cluster (e.g. -f was used). Please, remove the file(s) manually.
            """
            ),
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'D1' prefers node 'node1' with score 100 (id: location-D1-node1-100)
                  resource 'D1' prefers node 'guest1' with score 200 (id: location-D1-guest1-200)
                  resource 'D2' avoids node 'node2' with score 300 (id: location-D2-node2--300)
                  resource 'D2' avoids node 'guest1' with score 400 (id: location-D2-guest1--400)
                """
            ),
        )

    def test_duplicate_order(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order D1 then D2".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order D1 then D2".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  start D1 then start D2 (kind:Mandatory) (id:order-D1-D2-mandatory)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then start D2".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  start D1 then start D2 (kind:Mandatory) (id:order-D1-D2-mandatory)
  start D1 then start D2 (kind:Mandatory) (id:order-D1-D2-mandatory-1)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D1 then start D2 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D2 then start D5".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D2 D5 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D2 then start D5".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  start D2 then start D5 (kind:Mandatory) (id:order-D2-D5-mandatory)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order start D2 then start D5 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D2 D5 (kind: Mandatory) (Options: first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stop D5 then stop D6".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D5 D6 (kind: Mandatory) (Options: first-action=stop then-action=stop)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stop D5 then stop D6".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  stop D5 then stop D6 (kind:Mandatory) (id:order-D5-D6-mandatory)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order stop D5 then stop D6 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D5 D6 (kind: Mandatory) (Options: first-action=stop then-action=stop)\n",
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Order Constraints:
                  start resource 'D1' then start resource 'D2' (id: order-D1-D2-mandatory)
                  start resource 'D1' then start resource 'D2' (id: order-D1-D2-mandatory-1)
                  start resource 'D1' then start resource 'D2' (id: order-D1-D2-mandatory-2)
                  start resource 'D2' then start resource 'D5' (id: order-D2-D5-mandatory)
                  start resource 'D2' then start resource 'D5' (id: order-D2-D5-mandatory-1)
                  stop resource 'D5' then stop resource 'D6' (id: order-D5-D6-mandatory)
                  stop resource 'D5' then stop resource 'D6' (id: order-D5-D6-mandatory-1)
                """
            ),
        )

    def test_duplicate_colocation(self):
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2".split(),
        )
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  D1 with D2 (score:INFINITY) (id:colocation-D1-D2-INFINITY)
""",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 score=50".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  D1 with D2 (score:INFINITY) (id:colocation-D1-D2-INFINITY)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 score=50 --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add started D1 with started D2".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  D1 with D2 (score:INFINITY) (id:colocation-D1-D2-INFINITY)
  D1 with D2 (score:50) (id:colocation-D1-D2-50)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add started D1 with started D2 --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add started D2 with started D5".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add stopped D2 with stopped D5".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add stopped D2 with stopped D5".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            """\
Error: duplicate constraint already exists, use --force to override
  D2 with D5 (score:INFINITY) (rsc-role:Stopped) (with-rsc-role:Stopped) (id:colocation-D2-D5-INFINITY-1)
""",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add stopped D2 with stopped D5 --force".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Colocation Constraints:
                  resource 'D1' with resource 'D2' (id: colocation-D1-D2-INFINITY)
                    score=INFINITY
                  resource 'D1' with resource 'D2' (id: colocation-D1-D2-50)
                    score=50
                  Started resource 'D1' with Started resource 'D2' (id: colocation-D1-D2-INFINITY-1)
                    score=INFINITY
                  Started resource 'D2' with Started resource 'D5' (id: colocation-D2-D5-INFINITY)
                    score=INFINITY
                  Stopped resource 'D2' with Stopped resource 'D5' (id: colocation-D2-D5-INFINITY-1)
                    score=INFINITY
                  Stopped resource 'D2' with Stopped resource 'D5' (id: colocation-D2-D5-INFINITY-2)
                    score=INFINITY
                """
            ),
        )

    def test_duplicate_set_constraints(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set D1 D2".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set D1 D2".split()
        )
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: order_set_D1D2\n"
                "    Resource Set: order_set_D1D2_set\n"
                "      Resources: 'D1', 'D2'\n"
                "Error: Duplicate constraint already exists, use --force to "
                "override\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 --force".split(),
        )
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: order_set_D1D2\n"
                "    Resource Set: order_set_D1D2_set\n"
                "      Resources: 'D1', 'D2'\n"
                "Warning: Duplicate constraint already exists\n"
            ),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 set D5 D6".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 set D5 D6".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: order_set_D1D2D5\n"
                "    Resource Set: order_set_D1D2D5_set\n"
                "      Resources: 'D1', 'D2'\n"
                "    Resource Set: order_set_D1D2D5_set-1\n"
                "      Resources: 'D5', 'D6'\n"
                "Error: Duplicate constraint already exists, use --force to "
                "override\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 set D5 D6 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: order_set_D1D2D5\n"
                "    Resource Set: order_set_D1D2D5_set\n"
                "      Resources: 'D1', 'D2'\n"
                "    Resource Set: order_set_D1D2D5_set-1\n"
                "      Resources: 'D5', 'D6'\n"
                "Warning: Duplicate constraint already exists\n"
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint colocation set D1 D2".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint colocation set D1 D2".split()
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: colocation_set_D1D2\n"
                "    score=INFINITY\n"
                "    Resource Set: colocation_set_D1D2_set\n"
                "      Resources: 'D1', 'D2'\n"
                "Error: Duplicate constraint already exists, use --force to "
                "override\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: colocation_set_D1D2\n"
                "    score=INFINITY\n"
                "    Resource Set: colocation_set_D1D2_set\n"
                "      Resources: 'D1', 'D2'\n"
                "Warning: Duplicate constraint already exists\n"
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 set D5 D6".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 set D5 D6".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: colocation_set_D1D2D5\n"
                "    score=INFINITY\n"
                "    Resource Set: colocation_set_D1D2D5_set\n"
                "      Resources: 'D1', 'D2'\n"
                "    Resource Set: colocation_set_D1D2D5_set-1\n"
                "      Resources: 'D5', 'D6'\n"
                "Error: Duplicate constraint already exists, use --force to "
                "override\n" + ERRORS_HAVE_OCCURRED
            ),
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 set D5 D6 --force".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            (
                "Duplicate constraints:\n"
                "  Set Constraint: colocation_set_D1D2D5\n"
                "    score=INFINITY\n"
                "    Resource Set: colocation_set_D1D2D5_set\n"
                "      Resources: 'D1', 'D2'\n"
                "    Resource Set: colocation_set_D1D2D5_set-1\n"
                "      Resources: 'D5', 'D6'\n"
                "Warning: Duplicate constraint already exists\n"
            ),
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint colocation set D6 D1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name, "constraint order set D6 D1".split()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Colocation Set Constraints:
                  Set Constraint: colocation_set_D1D2
                    score=INFINITY
                    Resource Set: colocation_set_D1D2_set
                      Resources: 'D1', 'D2'
                  Set Constraint: colocation_set_D1D2-1
                    score=INFINITY
                    Resource Set: colocation_set_D1D2-1_set
                      Resources: 'D1', 'D2'
                  Set Constraint: colocation_set_D1D2D5
                    score=INFINITY
                    Resource Set: colocation_set_D1D2D5_set
                      Resources: 'D1', 'D2'
                    Resource Set: colocation_set_D1D2D5_set-1
                      Resources: 'D5', 'D6'
                  Set Constraint: colocation_set_D1D2D5-1
                    score=INFINITY
                    Resource Set: colocation_set_D1D2D5-1_set
                      Resources: 'D1', 'D2'
                    Resource Set: colocation_set_D1D2D5-1_set-1
                      Resources: 'D5', 'D6'
                  Set Constraint: colocation_set_D6D1
                    score=INFINITY
                    Resource Set: colocation_set_D6D1_set
                      Resources: 'D1', 'D6'
                Order Set Constraints:
                  Set Constraint: order_set_D1D2
                    Resource Set: order_set_D1D2_set
                      Resources: 'D1', 'D2'
                  Set Constraint: order_set_D1D2-1
                    Resource Set: order_set_D1D2-1_set
                      Resources: 'D1', 'D2'
                  Set Constraint: order_set_D1D2D5
                    Resource Set: order_set_D1D2D5_set
                      Resources: 'D1', 'D2'
                    Resource Set: order_set_D1D2D5_set-1
                      Resources: 'D5', 'D6'
                  Set Constraint: order_set_D1D2D5-1
                    Resource Set: order_set_D1D2D5-1_set
                      Resources: 'D1', 'D2'
                    Resource Set: order_set_D1D2D5-1_set-1
                      Resources: 'D5', 'D6'
                  Set Constraint: order_set_D6D1
                    Resource Set: order_set_D6D1_set
                      Resources: 'D1', 'D6'
                """
            ),
        )

    def test_constraints_custom_id(self):  # noqa: PLR0915
        # pylint: disable=too-many-statements
        self.fixture_resources()
        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 id=1id".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid constraint id '1id', '1' is not a valid first character for a constraint id\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 id=id1".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D1 with D2 id=id1".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: id 'id1' is already in use, please specify another one\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation add D2 with D1 score=100 id=id2".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 setoptions id=3id".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid constraint id '3id', '3' is not a valid first character for a constraint id\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 setoptions id=id3".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D1 D2 setoptions id=id3".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Error: 'id3' already exists\n")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint colocation set D2 D1 setoptions score=100 id=id4".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions id=5id".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid constraint id '5id', '5' is not a valid first character for a constraint id\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions id=id5".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D1 D2 setoptions id=id5".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Error: 'id5' already exists\n")
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order set D2 D1 setoptions kind=Mandatory id=id6".split(),
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 id=7id".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: invalid constraint id '7id', '7' is not a valid first character for a constraint id\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 id=id7".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D1 D2 (kind: Mandatory) (Options: id=id7 first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D1 then D2 id=id7".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Error: id 'id7' is already in use, please specify another one\n",
        )
        self.assertEqual(retval, 1)

        stdout, stderr, retval = pcs(
            self.temp_cib.name,
            "constraint order D2 then D1 kind=Optional id=id8".split(),
        )
        self.assertEqual(stdout, "")
        ac(
            stderr,
            "Adding D2 D1 (kind: Optional) (Options: id=id8 first-action=start then-action=start)\n",
        )
        self.assertEqual(retval, 0)

        self.assert_pcs_success(
            "constraint --full".split(),
            stdout_full=outdent(
                """\
                Colocation Constraints:
                  resource 'D1' with resource 'D2' (id: id1)
                    score=INFINITY
                  resource 'D2' with resource 'D1' (id: id2)
                    score=100
                Colocation Set Constraints:
                  Set Constraint: id3
                    score=INFINITY
                    Resource Set: id3_set
                      Resources: 'D1', 'D2'
                  Set Constraint: id4
                    score=100
                    Resource Set: id4_set
                      Resources: 'D1', 'D2'
                Order Constraints:
                  start resource 'D1' then start resource 'D2' (id: id7)
                  start resource 'D2' then start resource 'D1' (id: id8)
                    kind=Optional
                Order Set Constraints:
                  Set Constraint: id5
                    Resource Set: id5_set
                      Resources: 'D1', 'D2'
                  Set Constraint: id6
                    kind=Mandatory
                    Resource Set: id6_set
                      Resources: 'D1', 'D2'
                """
            ),
        )


class ConstraintBaseTest(unittest.TestCase, AssertPcsMixin):
    empty_cib = rc("cib-empty-3.7.xml")

    def setUp(self):
        self.temp_cib = get_tmp_file("tier1_constraint")
        write_file_to_tmpfile(self.empty_cib, self.temp_cib)
        self.pcs_runner = PcsRunner(self.temp_cib.name)
        self.pcs_runner.mock_settings = get_mock_settings()
        self.assert_pcs_success("resource create A ocf:pcsmock:minimal".split())
        self.assert_pcs_success("resource create B ocf:pcsmock:minimal".split())

    def tearDown(self):
        self.temp_cib.close()


class CommonCreateWithSet(ConstraintBaseTest):
    def test_refuse_when_resource_does_not_exist(self):
        self.assert_pcs_fail(
            "constraint ticket set A C setoptions ticket=T".split(),
            "Error: 'C' does not exist\n" + ERRORS_HAVE_OCCURRED,
        )


class TicketCreateWithSet(ConstraintBaseTest):
    def test_create_ticket(self):
        self.assert_pcs_success(
            "constraint ticket set A B setoptions ticket=T loss-policy=fence".split()
        )

    def test_can_skip_loss_policy(self):
        self.assert_pcs_success(
            "constraint ticket set A B setoptions ticket=T".split()
        )
        self.assert_pcs_success(
            "constraint ticket config".split(),
            stdout_full=outdent(
                """\
                Ticket Set Constraints:
                  Set Constraint:
                    ticket=T
                    Resource Set:
                      Resources: 'A', 'B'
                """
            ),
        )

    def test_refuse_bad_loss_policy(self):
        self.assert_pcs_fail(
            "constraint ticket set A B setoptions ticket=T loss-policy=none".split(),
            (
                "Error: 'none' is not a valid loss-policy value, use 'demote', "
                + "'fence', 'freeze', 'stop'\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_refuse_when_ticket_option_is_missing(self):
        self.assert_pcs_fail(
            "constraint ticket set A B setoptions loss-policy=fence".split(),
            (
                "Error: required option 'ticket' is missing\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_refuse_when_option_is_invalid(self):
        self.assert_pcs_fail(
            "constraint ticket set A B setoptions loss-policy".split(),
            "Error: missing value of 'loss-policy' option\n",
        )


class TicketAdd(ConstraintBaseTest):
    def test_create_minimal(self):
        self.assert_pcs_success("constraint ticket add T A".split())
        self.assert_pcs_success(
            "constraint ticket config".split(),
            dedent(
                """\
                Ticket Constraints:
                  resource 'A' depends on ticket 'T'
                """
            ),
        )

    def test_create_all_options(self):
        self.assert_pcs_success(
            (
                f"constraint ticket add T {const.PCMK_ROLE_PROMOTED} A "
                "loss-policy=fence id=my-constraint"
            ).split()
        )
        self.assert_pcs_success(
            "constraint ticket config --full".split(),
            stdout_full=outdent(
                f"""\
                Ticket Constraints:
                  {const.PCMK_ROLE_PROMOTED} resource 'A' depends on ticket 'T' (id: my-constraint)
                    loss-policy=fence
                """
            ),
        )

    def test_refuse_bad_option(self):
        self.assert_pcs_fail(
            "constraint ticket add T A loss_policy=fence".split(),
            (
                "Error: invalid option 'loss_policy', allowed options are: "
                "'id', 'loss-policy'\n" + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_refuse_noexistent_resource_id(self):
        self.assert_pcs_fail(
            "constraint ticket add T Promoted AA loss-policy=fence".split(),
            "Error: 'AA' does not exist\n" + ERRORS_HAVE_OCCURRED,
        )

    def test_refuse_invalid_role(self):
        self.assert_pcs_fail(
            "constraint ticket add T bad-role A loss-policy=fence".split(),
            (
                "Error: 'bad-role' is not a valid role value, use {}\n".format(
                    format_list(const.PCMK_ROLES)
                )
                + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_refuse_legacy_role(self):
        self.assert_pcs_fail(
            "constraint ticket add T Master A loss-policy=fence".split(),
            (
                "Error: 'Master' is not a valid role value, use {}\n".format(
                    format_list(const.PCMK_ROLES)
                )
                + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_refuse_duplicate_ticket(self):
        self.assert_pcs_success(
            (
                f"constraint ticket add T {const.PCMK_ROLE_UNPROMOTED} A "
                "loss-policy=fence"
            ).split(),
        )
        role = str(const.PCMK_ROLE_UNPROMOTED).lower()
        self.assert_pcs_fail(
            f"constraint ticket add T {role} A loss-policy=fence".split(),
            (
                "Duplicate constraints:\n"
                f"  {const.PCMK_ROLE_UNPROMOTED} resource 'A' depends on ticket 'T' (id: ticket-T-A-{const.PCMK_ROLE_UNPROMOTED})\n"
                "    loss-policy=fence\n"
                "Error: Duplicate constraint already exists, use --force to override\n"
                + ERRORS_HAVE_OCCURRED
            ),
        )

    def test_accept_duplicate_ticket_with_force(self):
        role = str(const.PCMK_ROLE_PROMOTED).lower()
        self.assert_pcs_success(
            f"constraint ticket add T {role} A loss-policy=fence".split(),
        )
        promoted_role = const.PCMK_ROLE_PROMOTED
        self.assert_pcs_success(
            (
                f"constraint ticket add T {const.PCMK_ROLE_PROMOTED} A "
                "loss-policy=fence --force"
            ).split(),
            stderr_full=[
                "Duplicate constraints:",
                f"  {promoted_role} resource 'A' depends on ticket 'T' (id: ticket-T-A-{promoted_role})",
                "    loss-policy=fence\n"
                "Warning: Duplicate constraint already exists",
            ],
        )
        self.assert_pcs_success(
            "constraint ticket config".split(),
            stdout_full=outdent(
                f"""\
                Ticket Constraints:
                  {promoted_role} resource 'A' depends on ticket 'T'
                    loss-policy=fence
                  {promoted_role} resource 'A' depends on ticket 'T'
                    loss-policy=fence
                """
            ),
        )


class TicketDeleteRemoveTest(ConstraintBaseTest):
    command = None

    def _test_usage(self):
        self.assert_pcs_fail(
            ["constraint", "ticket", self.command],
            stderr_start=outdent(
                f"""
                Usage: pcs constraint [constraints]...
                    ticket {self.command} <"""
            ),
        )

    def _test_remove_multiple_tickets(self):
        # fixture
        self.assert_pcs_success("constraint ticket add T A".split())
        self.assert_pcs_success(
            "constraint ticket add T A --force".split(),
            stderr_full=[
                "Duplicate constraints:",
                "  resource 'A' depends on ticket 'T' (id: ticket-T-A)",
                "Warning: Duplicate constraint already exists",
            ],
        )
        self.assert_pcs_success(
            "constraint ticket set A B setoptions ticket=T".split()
        )
        self.assert_pcs_success(
            "constraint ticket set A setoptions ticket=T".split()
        )
        self.assert_pcs_success(
            "constraint ticket config".split(),
            stdout_full=outdent(
                """\
                Ticket Constraints:
                  resource 'A' depends on ticket 'T'
                  resource 'A' depends on ticket 'T'
                Ticket Set Constraints:
                  Set Constraint:
                    ticket=T
                    Resource Set:
                      Resources: 'A', 'B'
                  Set Constraint:
                    ticket=T
                    Resource Set:
                      Resources: 'A'
                """
            ),
        )

        # test
        self.assert_pcs_success(
            ["constraint", "ticket", self.command, "T", "A"]
        )

        self.assert_pcs_success(
            "constraint ticket config".split(),
            stdout_full=outdent(
                """\
                Ticket Set Constraints:
                  Set Constraint:
                    ticket=T
                    Resource Set:
                      Resources: 'B'
                """
            ),
        )

    def _test_fail_when_no_matching_ticket_constraint_here(self):
        self.assert_pcs_success(
            "constraint ticket config".split(),
        )
        self.assert_pcs_fail(
            ["constraint", "ticket", self.command, "T", "A"],
            ["Error: no matching ticket constraint found"],
        )


class TicketDeleteTest(
    TicketDeleteRemoveTest, metaclass=ParametrizedTestMetaClass
):
    command = "delete"


class TicketRemoveTest(
    TicketDeleteRemoveTest, metaclass=ParametrizedTestMetaClass
):
    command = "remove"


class TicketShow(ConstraintBaseTest):
    def test_show_set(self):
        self.assert_pcs_success(
            "constraint ticket set A B setoptions ticket=T".split()
        )
        role = str(const.PCMK_ROLE_PROMOTED).lower()
        self.assert_pcs_success(
            f"constraint ticket add T {role} A loss-policy=fence".split(),
        )
        self.assert_pcs_success(
            "constraint ticket config".split(),
            outdent(
                f"""\
                Ticket Constraints:
                  {const.PCMK_ROLE_PROMOTED} resource 'A' depends on ticket 'T'
                    loss-policy=fence
                Ticket Set Constraints:
                  Set Constraint:
                    ticket=T
                    Resource Set:
                      Resources: 'A', 'B'
                """
            ),
        )


class ConstraintEffect(
    unittest.TestCase,
    get_assert_pcs_effect_mixin(
        lambda cib: etree.tostring(
            etree.parse(cib).findall(".//constraints")[0]
        )
    ),
):
    empty_cib = rc("cib-empty.xml")

    def setUp(self):
        self.temp_cib = get_tmp_file("tier1_constraint")
        write_file_to_tmpfile(self.empty_cib, self.temp_cib)
        self.pcs_runner = PcsRunner(self.temp_cib.name)
        self.pcs_runner.mock_settings = get_mock_settings()

    def tearDown(self):
        self.temp_cib.close()

    def fixture_primitive(self, name):
        self.assert_pcs_success(
            ["resource", "create", name, "ocf:pcsmock:minimal"]
        )


class LocationTypeId(ConstraintEffect):
    # This was written while implementing rsc-pattern to location constraints.
    # Thus it focuses only the new feature (rsc-pattern) and it is NOT a
    # complete test of location constraints. Instead it relies on legacy tests
    # to test location constraints with plain resource name.
    def test_prefers(self):
        self.fixture_primitive("A")
        self.assert_effect(
            [
                "constraint location A prefers node1".split(),
                "constraint location %A prefers node1".split(),
                "constraint location resource%A prefers node1".split(),
            ],
            """<constraints>
                <rsc_location id="location-A-node1-INFINITY" node="node1"
                    rsc="A" score="INFINITY"
                />
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_avoids(self):
        self.fixture_primitive("A")
        self.assert_effect(
            [
                "constraint location A avoids node1".split(),
                "constraint location %A avoids node1".split(),
                "constraint location resource%A avoids node1".split(),
            ],
            """<constraints>
                <rsc_location id="location-A-node1--INFINITY" node="node1"
                    rsc="A" score="-INFINITY"
                />
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_add_deprecated(self):
        self.fixture_primitive("A")
        self.assert_effect(
            [
                "constraint location add my-id A node1 INFINITY".split(),
                "constraint location add my-id %A node1 INFINITY".split(),
                "constraint location add my-id resource%A node1 INFINITY".split(),
            ],
            """<constraints>
                <rsc_location id="my-id" node="node1" rsc="A" score="INFINITY"/>
            </constraints>""",
            stderr_full=(
                DEPRECATED_STANDALONE_SCORE
                + LOCATION_NODE_VALIDATION_SKIP_WARNING
            ),
        )

    def test_add(self):
        self.fixture_primitive("A")
        self.assert_effect(
            [
                "constraint location add my-id A node1 score=INFINITY".split(),
                "constraint location add my-id %A node1 score=INFINITY".split(),
                "constraint location add my-id resource%A node1 score=INFINITY".split(),
            ],
            """<constraints>
                <rsc_location id="my-id" node="node1" rsc="A" score="INFINITY"/>
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )


class LocationTypePattern(ConstraintEffect):
    # This was written while implementing rsc-pattern to location constraints.
    # Thus it focuses only the new feature (rsc-pattern) and it is NOT a
    # complete test of location constraints. Instead it relies on legacy tests
    # to test location constraints with plain resource name.
    def test_prefers(self):
        self.assert_effect(
            "constraint location regexp%res_[0-9] prefers node1".split(),
            """<constraints>
                <rsc_location id="location-res_0-9-node1-INFINITY" node="node1"
                    rsc-pattern="res_[0-9]" score="INFINITY"
                />
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_avoids(self):
        self.assert_effect(
            "constraint location regexp%res_[0-9] avoids node1".split(),
            """<constraints>
                <rsc_location id="location-res_0-9-node1--INFINITY" node="node1"
                    rsc-pattern="res_[0-9]" score="-INFINITY"
                />
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_add_deprecated(self):
        self.assert_effect(
            "constraint location add my-id regexp%res_[0-9] node1 INFINITY".split(),
            """<constraints>
                <rsc_location id="my-id" node="node1" rsc-pattern="res_[0-9]"
                    score="INFINITY"
                />
            </constraints>""",
            stderr_full=(
                DEPRECATED_STANDALONE_SCORE
                + LOCATION_NODE_VALIDATION_SKIP_WARNING
            ),
        )

    def test_add(self):
        self.assert_effect(
            "constraint location add my-id regexp%res_[0-9] node1 score=INFINITY".split(),
            """<constraints>
                <rsc_location id="my-id" node="node1" rsc-pattern="res_[0-9]"
                    score="INFINITY"
                />
            </constraints>""",
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )


@skip_unless_crm_rule()
class LocationShowWithPattern(ConstraintBaseTest):
    # This was written while implementing rsc-pattern to location constraints.
    # Thus it focuses only the new feature (rsc-pattern) and it is NOT a
    # complete test of location constraints. Instead it relies on legacy tests
    # to test location constraints with plain resource name.
    def fixture(self):
        self.assert_pcs_success_all(
            [
                "resource create R1 ocf:pcsmock:minimal".split(),
                "resource create R2 ocf:pcsmock:minimal".split(),
                "resource create R3 ocf:pcsmock:minimal".split(),
                "constraint location R1 prefers node1 node2=20".split(),
                "constraint location R1 avoids node3=30 node4".split(),
                "constraint location R2 prefers node3 node4=20".split(),
                "constraint location R2 avoids node1=30 node2".split(),
                "constraint location regexp%R_[0-9]+ prefers node1 node2=20".split(),
                "constraint location regexp%R_[0-9]+ avoids node3=30".split(),
                "constraint location regexp%R_[a-z]+ avoids node3=30".split(),
                "constraint location add my-id1 R3 node1 -INFINITY resource-discovery=never".split(),
                "constraint location add my-id2 R3 node2 -INFINITY resource-discovery=never".split(),
                "constraint location add my-id3 regexp%R_[0-9]+ node4 -INFINITY resource-discovery=never".split(),
                "constraint location regexp%R_[0-9]+ rule score=20 defined pingd".split(),
            ]
        )

    def test_show(self):
        self.fixture()
        self.assert_pcs_success(
            "constraint location config --all --full".split(),
            outdent(
                """\
                Location Constraints:
                  resource 'R1' prefers node 'node1' with score INFINITY (id: location-R1-node1-INFINITY)
                  resource 'R1' prefers node 'node2' with score 20 (id: location-R1-node2-20)
                  resource 'R1' avoids node 'node3' with score 30 (id: location-R1-node3--30)
                  resource 'R1' avoids node 'node4' with score INFINITY (id: location-R1-node4--INFINITY)
                  resource 'R2' prefers node 'node3' with score INFINITY (id: location-R2-node3-INFINITY)
                  resource 'R2' prefers node 'node4' with score 20 (id: location-R2-node4-20)
                  resource 'R2' avoids node 'node1' with score 30 (id: location-R2-node1--30)
                  resource 'R2' avoids node 'node2' with score INFINITY (id: location-R2-node2--INFINITY)
                  resource pattern 'R_[0-9]+' prefers node 'node1' with score INFINITY (id: location-R_0-9-node1-INFINITY)
                  resource pattern 'R_[0-9]+' prefers node 'node2' with score 20 (id: location-R_0-9-node2-20)
                  resource pattern 'R_[0-9]+' avoids node 'node3' with score 30 (id: location-R_0-9-node3--30)
                  resource pattern 'R_[a-z]+' avoids node 'node3' with score 30 (id: location-R_a-z-node3--30)
                  resource 'R3' avoids node 'node1' with score INFINITY (id: my-id1)
                    resource-discovery=never
                  resource 'R3' avoids node 'node2' with score INFINITY (id: my-id2)
                    resource-discovery=never
                  resource pattern 'R_[0-9]+' avoids node 'node4' with score INFINITY (id: my-id3)
                    resource-discovery=never
                  resource pattern 'R_[0-9]+' (id: location-R_0-9)
                    Rules:
                      Rule: boolean-op=and score=20 (id: location-R_0-9-rule)
                        Expression: defined pingd (id: location-R_0-9-rule-expr)
                """
            ),
        )

        self.assert_pcs_success(
            "constraint location config".split(),
            stdout_full=outdent(
                """\
                Location Constraints:
                  resource 'R1' prefers node 'node1' with score INFINITY
                  resource 'R1' prefers node 'node2' with score 20
                  resource 'R1' avoids node 'node3' with score 30
                  resource 'R1' avoids node 'node4' with score INFINITY
                  resource 'R2' prefers node 'node3' with score INFINITY
                  resource 'R2' prefers node 'node4' with score 20
                  resource 'R2' avoids node 'node1' with score 30
                  resource 'R2' avoids node 'node2' with score INFINITY
                  resource pattern 'R_[0-9]+' prefers node 'node1' with score INFINITY
                  resource pattern 'R_[0-9]+' prefers node 'node2' with score 20
                  resource pattern 'R_[0-9]+' avoids node 'node3' with score 30
                  resource pattern 'R_[a-z]+' avoids node 'node3' with score 30
                  resource 'R3' avoids node 'node1' with score INFINITY
                    resource-discovery=never
                  resource 'R3' avoids node 'node2' with score INFINITY
                    resource-discovery=never
                  resource pattern 'R_[0-9]+' avoids node 'node4' with score INFINITY
                    resource-discovery=never
                  resource pattern 'R_[0-9]+'
                    Rules:
                      Rule: boolean-op=and score=20
                        Expression: defined pingd
                """
            ),
        )

        self.assert_pcs_success(
            "constraint location config nodes --full".split(),
            outdent(
                """\
                Location Constraints:
                  Node: node1
                    Preferred by:
                      resource 'R1' with score INFINITY (id: location-R1-node1-INFINITY)
                      resource pattern 'R_[0-9]+' with score INFINITY (id: location-R_0-9-node1-INFINITY)
                    Avoided by:
                      resource 'R2' with score 30 (id: location-R2-node1--30)
                      resource 'R3' with score INFINITY (id: my-id1)
                  Node: node2
                    Preferred by:
                      resource 'R1' with score 20 (id: location-R1-node2-20)
                      resource pattern 'R_[0-9]+' with score 20 (id: location-R_0-9-node2-20)
                    Avoided by:
                      resource 'R2' with score INFINITY (id: location-R2-node2--INFINITY)
                      resource 'R3' with score INFINITY (id: my-id2)
                  Node: node3
                    Preferred by:
                      resource 'R2' with score INFINITY (id: location-R2-node3-INFINITY)
                    Avoided by:
                      resource 'R1' with score 30 (id: location-R1-node3--30)
                      resource pattern 'R_[0-9]+' with score 30 (id: location-R_0-9-node3--30)
                      resource pattern 'R_[a-z]+' with score 30 (id: location-R_a-z-node3--30)
                  Node: node4
                    Preferred by:
                      resource 'R2' with score 20 (id: location-R2-node4-20)
                    Avoided by:
                      resource 'R1' with score INFINITY (id: location-R1-node4--INFINITY)
                      resource pattern 'R_[0-9]+' with score INFINITY (id: my-id3)
                """
            ),
            stderr_full=WARN_WITH_RULES_SKIP,
        )

        self.assert_pcs_success(
            "constraint location config nodes node2".split(),
            outdent(
                """\
                Location Constraints:
                  Node: node2
                    Preferred by:
                      resource 'R1' with score 20
                      resource pattern 'R_[0-9]+' with score 20
                    Avoided by:
                      resource 'R2' with score INFINITY
                      resource 'R3' with score INFINITY
                """
            ),
        )

        self.assert_pcs_success(
            "constraint location config resources regexp%R_[0-9]+".split(),
            outdent(
                """\
                Location Constraints:
                  Resource pattern: R_[0-9]+
                    Prefers:
                      node 'node1' with score INFINITY
                      node 'node2' with score 20
                    Avoids:
                      node 'node3' with score 30
                      node 'node4' with score INFINITY
                    Constraint:
                      Rules:
                        Rule: boolean-op=and score=20
                          Expression: defined pingd
                """
            ),
        )


class Bundle(ConstraintEffect):
    def setUp(self):
        super().setUp()
        self.fixture_bundle("B")

    def fixture_primitive(self, name, bundle=None):
        if not bundle:
            super().fixture_primitive(name)
            return
        self.assert_pcs_success(
            [
                "resource",
                "create",
                name,
                "ocf:pcsmock:minimal",
                "bundle",
                bundle,
            ]
        )

    def fixture_bundle(self, name):
        self.assert_pcs_success(
            [
                "resource",
                "bundle",
                "create",
                name,
                "container",
                "docker",
                "image=pcs:test",
                "network",
                "control-port=1234",
            ]
        )


class BundleLocation(Bundle):
    def test_bundle_prefers(self):
        self.assert_effect(
            "constraint location B prefers node1".split(),
            """
                <constraints>
                    <rsc_location id="location-B-node1-INFINITY" node="node1"
                        rsc="B" score="INFINITY"
                    />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_bundle_avoids(self):
        self.assert_effect(
            "constraint location B avoids node1".split(),
            """
                <constraints>
                    <rsc_location id="location-B-node1--INFINITY" node="node1"
                        rsc="B" score="-INFINITY"
                    />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_bundle_location(self):
        self.assert_effect(
            "constraint location add id B node1 score=100".split(),
            """
                <constraints>
                    <rsc_location id="id" node="node1" rsc="B" score="100" />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_primitive_prefers(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint location R prefers node1".split(),
            (
                LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: R is a bundle resource, you should use the bundle id: "
                "B when adding constraints. Use --force to override.\n"
            ),
        )

    def test_primitive_prefers_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint location R prefers node1 --force".split(),
            """
                <constraints>
                    <rsc_location id="location-R-node1-INFINITY" node="node1"
                        rsc="R" score="INFINITY"
                    />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_primitive_avoids(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint location R avoids node1".split(),
            (
                LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: R is a bundle resource, you should use the bundle id: "
                "B when adding constraints. Use --force to override.\n"
            ),
        )

    def test_primitive_avoids_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint location R avoids node1 --force".split(),
            """
                <constraints>
                    <rsc_location id="location-R-node1--INFINITY" node="node1"
                        rsc="R" score="-INFINITY"
                    />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def test_primitive_location(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint location add id R node1 score=100".split(),
            (
                LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: R is a bundle resource, you should use the bundle id: "
                "B when adding constraints. Use --force to override.\n"
            ),
        )

    def test_primitive_location_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint location add id R node1 score=100 --force".split(),
            """
                <constraints>
                    <rsc_location id="id" node="node1" rsc="R" score="100" />
                </constraints>
            """,
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )


class BundleColocation(Bundle):
    def setUp(self):
        super().setUp()
        self.fixture_primitive("X")

    def test_bundle(self):
        self.assert_effect(
            "constraint colocation add B with X".split(),
            """
                <constraints>
                    <rsc_colocation id="colocation-B-X-INFINITY"
                        rsc="B" with-rsc="X" score="INFINITY" />
                </constraints>
            """,
        )

    def test_primitive(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint colocation add R with X".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints. Use --force to override.\n",
        )

    def test_primitive_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint colocation add R with X --force".split(),
            """
                <constraints>
                    <rsc_colocation id="colocation-R-X-INFINITY"
                        rsc="R" with-rsc="X" score="INFINITY" />
                </constraints>
            """,
        )

    def test_bundle_set(self):
        self.assert_effect(
            "constraint colocation set B X".split(),
            """
                <constraints>
                    <rsc_colocation id="colocation_set_BBXX" score="INFINITY">
                        <resource_set id="colocation_set_BBXX_set">
                            <resource_ref id="B" />
                            <resource_ref id="X" />
                        </resource_set>
                    </rsc_colocation>
                </constraints>
            """,
        )

    def test_primitive_set(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint colocation set R X".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )

    def test_primitive_set_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint colocation set R X --force".split(),
            """
                <constraints>
                    <rsc_colocation id="colocation_set_RRXX" score="INFINITY">
                        <resource_set id="colocation_set_RRXX_set">
                            <resource_ref id="R" />
                            <resource_ref id="X" />
                        </resource_set>
                    </rsc_colocation>
                </constraints>
            """,
            stderr_full=(
                "Warning: R is a bundle resource, you should use the bundle "
                "id: B when adding constraints\n"
            ),
        )


class BundleOrder(Bundle):
    def setUp(self):
        super().setUp()
        self.fixture_primitive("X")

    def test_bundle(self):
        self.assert_effect(
            "constraint order B then X".split(),
            """
                <constraints>
                    <rsc_order id="order-B-X-mandatory"
                        first="B" first-action="start"
                        then="X" then-action="start" />
                </constraints>
            """,
            stderr_full=(
                "Adding B X (kind: Mandatory) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )

    def test_primitive(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint order R then X".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints. Use --force to override.\n",
        )

    def test_primitive_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint order R then X --force".split(),
            """
                <constraints>
                    <rsc_order id="order-R-X-mandatory"
                        first="R" first-action="start"
                        then="X" then-action="start" />
                </constraints>
            """,
            stderr_full=(
                "Adding R X (kind: Mandatory) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )

    def test_bundle_set(self):
        self.assert_effect(
            "constraint order set B X".split(),
            """
                <constraints>
                    <rsc_order id="order_set_BBXX">
                        <resource_set id="order_set_BBXX_set">
                            <resource_ref id="B" />
                            <resource_ref id="X" />
                        </resource_set>
                    </rsc_order>
                </constraints>
            """,
        )

    def test_primitive_set(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint order set R X".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )

    def test_primitive_set_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint order set R X --force".split(),
            """
                <constraints>
                    <rsc_order id="order_set_RRXX">
                        <resource_set id="order_set_RRXX_set">
                            <resource_ref id="R" />
                            <resource_ref id="X" />
                        </resource_set>
                    </rsc_order>
                </constraints>
            """,
            stderr_full=(
                "Warning: R is a bundle resource, you should use the bundle id: B "
                "when adding constraints\n"
            ),
        )


class BundleTicket(Bundle):
    def test_bundle(self):
        self.assert_effect(
            "constraint ticket add T B".split(),
            """
                <constraints>
                    <rsc_ticket id="ticket-T-B" rsc="B" ticket="T" />
                </constraints>
            """,
        )

    def test_primitive(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint ticket add T R".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )

    def test_primitive_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint ticket add T R --force".split(),
            """
                <constraints>
                    <rsc_ticket id="ticket-T-R" rsc="R" ticket="T" />
                </constraints>
            """,
            stderr_full=(
                "Warning: R is a bundle resource, you should use the bundle id: B "
                "when adding constraints\n"
            ),
        )

    def test_bundle_set(self):
        self.assert_effect(
            "constraint ticket set B setoptions ticket=T".split(),
            """
                <constraints>
                    <rsc_ticket id="ticket_set_BB" ticket="T">
                        <resource_set id="ticket_set_BB_set">
                            <resource_ref id="B" />
                        </resource_set>
                    </rsc_ticket>
                </constraints>
            """,
        )

    def test_primitive_set(self):
        self.fixture_primitive("R", "B")
        self.assert_pcs_fail(
            "constraint ticket set R setoptions ticket=T".split(),
            "Error: R is a bundle resource, you should use the bundle id: B "
            "when adding constraints, use --force to override\n"
            + ERRORS_HAVE_OCCURRED,
        )

    def test_primitive_set_force(self):
        self.fixture_primitive("R", "B")
        self.assert_effect(
            "constraint ticket set R setoptions ticket=T --force".split(),
            """
                <constraints>
                    <rsc_ticket id="ticket_set_RR" ticket="T">
                        <resource_set id="ticket_set_RR_set">
                            <resource_ref id="R" />
                        </resource_set>
                    </rsc_ticket>
                </constraints>
            """,
            stderr_full=(
                "Warning: R is a bundle resource, you should use the bundle id: B "
                "when adding constraints\n"
            ),
        )


NodeScore = namedtuple("NodeScore", "node score")


class LocationPrefersAvoidsMixin(
    get_assert_pcs_effect_mixin(
        lambda cib: etree.tostring(
            etree.parse(cib).findall(".//constraints")[0]
        )
    )
):
    def setUp(self):
        self.temp_cib = get_tmp_file("tier1_constraint_location")
        write_file_to_tmpfile(self.empty_cib, self.temp_cib)
        self.pcs_runner = PcsRunner(self.temp_cib.name)
        self.pcs_runner.mock_settings = get_mock_settings()
        self.command = "to-be-overridden"

    def tearDown(self):
        self.temp_cib.close()

    def xml_score(self, score):
        # pylint: disable=no-self-use
        return score if score else "INFINITY"

    @staticmethod
    def _unpack_node_score_list_to_cmd(node_score_list):
        return [
            "{node}{score}".format(
                node=item.node,
                score="" if item.score is None else f"={item.score}",
            )
            for item in node_score_list
        ]

    def _construct_xml(self, node_score_list):
        return "\n".join(
            [
                "<constraints>",
                "\n".join(
                    """
                <rsc_location id="location-dummy-{node}-{score}"
                node="{node}" rsc="dummy" score="{score}"/>
                """.format(
                        node=item.node,
                        score=self.xml_score(item.score),
                    )
                    for item in node_score_list
                ),
                "</constraints>",
            ]
        )

    def assert_success(self, node_score_list):
        assert self.command in {"prefers", "avoids"}
        self.fixture_primitive("dummy")
        self.assert_effect(
            (
                ["constraint", "location", "dummy", self.command]
                + self._unpack_node_score_list_to_cmd(node_score_list)
            ),
            self._construct_xml(node_score_list),
            stderr_full=LOCATION_NODE_VALIDATION_SKIP_WARNING,
        )

    def assert_failure(self, node_score_list, error_msg):
        assert self.command in {"prefers", "avoids"}
        self.fixture_primitive("dummy")
        self.assert_pcs_fail(
            (
                ["constraint", "location", "dummy", self.command]
                + self._unpack_node_score_list_to_cmd(node_score_list)
            ),
            LOCATION_NODE_VALIDATION_SKIP_WARNING + error_msg,
        )
        self.assert_resources_xml_in_cib("<constraints/>")

    def test_single_implicit_success(self):
        node1 = NodeScore("node1", None)
        self.assert_success([node1])

    def test_single_explicit_success(self):
        node1 = NodeScore("node1", "10")
        self.assert_success([node1])

    def test_multiple_implicit_success(self):
        node1 = NodeScore("node1", None)
        node2 = NodeScore("node2", None)
        node3 = NodeScore("node3", None)
        self.assert_success([node1, node2, node3])

    def test_multiple_mixed_success(self):
        node1 = NodeScore("node1", None)
        node2 = NodeScore("node2", "300")
        node3 = NodeScore("node3", None)
        self.assert_success([node1, node2, node3])

    def test_multiple_explicit_success(self):
        node1 = NodeScore("node1", "100")
        node2 = NodeScore("node2", "300")
        node3 = NodeScore("node3", "200")
        self.assert_success([node1, node2, node3])

    def test_empty_score(self):
        node1 = NodeScore("node1", "")
        self.assert_failure(
            [node1],
            "Error: invalid score '', use integer or INFINITY or -INFINITY\n",
        )

    def test_single_explicit_fail(self):
        node1 = NodeScore("node1", "aaa")
        self.assert_failure(
            [node1],
            "Error: invalid score 'aaa', use integer or INFINITY or -INFINITY\n",
        )

    def test_multiple_implicit_fail(self):
        node1 = NodeScore("node1", "whatever")
        node2 = NodeScore("node2", "dontcare")
        node3 = NodeScore("node3", "never")
        self.assert_failure(
            [node1, node2, node3],
            "Error: invalid score 'whatever', use integer or INFINITY or "
            "-INFINITY\n",
        )

    def test_multiple_mixed_fail(self):
        node1 = NodeScore("node1", None)
        node2 = NodeScore("node2", "invalid")
        node3 = NodeScore("node3", "200")
        self.assert_failure(
            [node1, node2, node3],
            "Error: invalid score 'invalid', use integer or INFINITY or "
            "-INFINITY\n",
        )


class LocationPrefers(ConstraintEffect, LocationPrefersAvoidsMixin):
    command = "prefers"


class LocationAvoids(ConstraintEffect, LocationPrefersAvoidsMixin):
    command = "avoids"

    def xml_score(self, score):
        score = super().xml_score(score)
        return score[1:] if score[0] == "-" else "-" + score


class LocationAdd(ConstraintEffect):
    def test_invalid_score_deprecated(self):
        self.assert_pcs_fail(
            "constraint location add location1 D1 rh7-1 bar".split(),
            (
                DEPRECATED_STANDALONE_SCORE
                + LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: invalid score 'bar', use integer or INFINITY or "
                "-INFINITY\n"
            ),
        )
        self.assert_resources_xml_in_cib("<constraints/>")

    def test_invalid_score(self):
        self.assert_pcs_fail(
            "constraint location add location1 D1 rh7-1 score=bar".split(),
            (
                LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: invalid score 'bar', use integer or INFINITY or "
                "-INFINITY\n"
            ),
        )
        self.assert_resources_xml_in_cib("<constraints/>")

    def test_invalid_location(self):
        self.assert_pcs_fail(
            "constraint location add loc:dummy D1 rh7-1 score=100".split(),
            (
                LOCATION_NODE_VALIDATION_SKIP_WARNING
                + "Error: invalid constraint id 'loc:dummy', ':' is not a valid "
                "character for a constraint id\n"
            ),
        )
        self.assert_resources_xml_in_cib("<constraints/>")


@skip_unless_crm_rule()
class ExpiredConstraints(ConstraintBaseTest):
    # pylint: disable=too-many-public-methods
    # Setting tomorrow to the day after tomorrow in case the tests run close to
    # midnight.
    _tomorrow = (datetime.date.today() + datetime.timedelta(days=2)).strftime(
        "%Y-%m-%d"
    )
    empty_cib = rc("cib-empty-3.9.xml")

    def fixture_group(self):
        self.assert_pcs_success(
            "resource create dummy1 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create dummy2 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource group add dummy_group dummy1 dummy2".split()
        )

    def fixture_primitive(self):
        self.assert_pcs_success(
            "resource create dummy ocf:pcsmock:minimal".split()
        )

    def test_crm_rule_missing(self):
        mock_settings = get_mock_settings()
        mock_settings["crm_rule_exec"] = ""
        self.pcs_runner = PcsRunner(
            self.temp_cib.name, mock_settings=mock_settings
        )
        self.fixture_primitive()
        self.assert_pcs_success(
            "constraint location dummy rule id=test-rule score=INFINITY".split()
            + ["date lt 2019-01-01"]
        )
        self.assert_pcs_success(
            ["constraint"],
            outdent(
                """\
                Location Constraints:
                  resource 'dummy'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Expression: date lt 2019-01-01
                """
            ),
            stderr_full=f"Warning: {CRM_RULE_MISSING_MSG}\n",
        )

    def assert_in_effect_primitive(self, flag_all, flag_full):
        self.fixture_primitive()
        self.assert_pcs_success(
            "constraint location dummy rule id=test-rule score=INFINITY".split()
            + ["date gt 2019-01-01"]
        )
        self.assert_pcs_success(
            (
                "constraint"
                f"{' --full' if flag_full else ''}"
                f"{' --all' if flag_all else ''}"
            ).split(),
            outdent(
                f"""\
                Location Constraints:
                  resource 'dummy'{" (id: location-dummy)" if flag_full else ""}
                    Rules:
                      Rule: boolean-op=and score=INFINITY{" (id: test-rule)" if flag_full else ""}
                        Expression: date gt 2019-01-01{" (id: test-rule-expr)" if flag_full else ""}
                """
            ),
        )

    def test_in_effect_primitive_plain(self):
        self.assert_in_effect_primitive(flag_full=False, flag_all=False)

    def test_in_effect_primitive_full(self):
        self.assert_in_effect_primitive(flag_full=True, flag_all=False)

    def test_in_effect_primitive_all(self):
        self.assert_in_effect_primitive(flag_full=False, flag_all=True)

    def test_in_effect_primitive_full_all(self):
        self.assert_in_effect_primitive(flag_full=True, flag_all=True)

    def test_in_effect_group_plain(self):
        self.fixture_group()
        self.assert_pcs_success(
            "constraint location dummy_group rule id=test-rule score=INFINITY".split()
            + ["date gt 2019-01-01"]
        )
        self.assert_pcs_success(
            ["constraint"],
            outdent(
                """\
                Location Constraints:
                  resource 'dummy_group'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Expression: date gt 2019-01-01
                """
            ),
        )

    def fixture_expired_primitive_plain(self):
        self.fixture_primitive()
        self.assert_pcs_success(
            "constraint location dummy rule id=test-rule score=INFINITY".split()
            + ["date lt 2019-01-01"]
        )

    def test_expired_primitive_plain(self):
        self.fixture_expired_primitive_plain()
        self.assert_pcs_success(["constraint"])

    def test_expired_primitive_full(self):
        self.fixture_expired_primitive_plain()
        self.assert_pcs_success("constraint --full".split())

    def test_expired_primitive_all(self):
        self.fixture_expired_primitive_plain()
        self.assert_pcs_success(
            "constraint --all".split(),
            outdent(
                """\
                Location Constraints:
                  resource 'dummy'
                    Rules:
                      Rule (expired): boolean-op=and score=INFINITY
                        Expression: date lt 2019-01-01
                """
            ),
        )

    def test_expired_primitive_full_all(self):
        self.fixture_expired_primitive_plain()
        self.assert_pcs_success(
            "constraint --full --all".split(),
            outdent(
                """\
                Location Constraints:
                  resource 'dummy' (id: location-dummy)
                    Rules:
                      Rule (expired): boolean-op=and score=INFINITY (id: test-rule)
                        Expression: date lt 2019-01-01 (id: test-rule-expr)
                """
            ),
        )

    def test_expired_group_plain(self):
        self.fixture_group()
        self.assert_pcs_success(
            "constraint location dummy_group rule id=test-rule score=INFINITY".split()
            + ["date lt 2019-01-01"]
        )
        self.assert_pcs_success(["constraint"])

    def assert_indeterminate_primitive(self, flag_full, flag_all):
        self.fixture_primitive()
        self.assert_pcs_success(
            "constraint location dummy rule id=test-rule score=INFINITY".split()
            + ["date eq 2019-01-01 or date eq 2019-03-01"]
        )
        self.assert_pcs_success(
            (
                "constraint"
                f"{' --full' if flag_full else ''}"
                f"{' --all' if flag_all else ''}"
            ).split(),
            outdent(
                f"""\
                Location Constraints:
                  resource 'dummy'{" (id: location-dummy)" if flag_full else ""}
                    Rules:
                      Rule: boolean-op=or score=INFINITY{" (id: test-rule)" if flag_full else ""}
                        Expression: date eq 2019-01-01{" (id: test-rule-expr)" if flag_full else ""}
                        Expression: date eq 2019-03-01{" (id: test-rule-expr-1)" if flag_full else ""}
                """
            ),
        )

    def test_indeterminate_primitive_plain(self):
        self.assert_indeterminate_primitive(flag_full=False, flag_all=False)

    def test_indeterminate_primitive_full(self):
        self.assert_indeterminate_primitive(flag_full=True, flag_all=False)

    def test_indeterminate_primitive_all(self):
        self.assert_indeterminate_primitive(flag_full=False, flag_all=True)

    def test_indeterminate_primitive_full_all(self):
        self.assert_indeterminate_primitive(flag_full=True, flag_all=True)

    def test_indeterminate_group_plain(self):
        self.fixture_group()
        self.assert_pcs_success(
            "constraint location dummy_group rule id=test-rule score=INFINITY".split()
            + ["date eq 2019-01-01 or date eq 2019-03-01"]
        )
        self.assert_pcs_success(
            ["constraint"],
            outdent(
                """\
                Location Constraints:
                  resource 'dummy_group'
                    Rules:
                      Rule: boolean-op=or score=INFINITY
                        Expression: date eq 2019-01-01
                        Expression: date eq 2019-03-01
                """
            ),
        )

    def assert_not_yet_in_effect_primitive(self, flag_full, flag_all):
        self.fixture_primitive()
        self.assert_pcs_success(
            "constraint location dummy rule id=test-rule score=INFINITY".split()
            + [f"date gt {self._tomorrow}"]
        )
        self.assert_pcs_success(
            (
                "constraint"
                f"{' --full' if flag_full else ''}"
                f"{' --all' if flag_all else ''}"
            ).split(),
            outdent(
                f"""\
                Location Constraints:
                  resource 'dummy'{" (id: location-dummy)" if flag_full else ""}
                    Rules:
                      Rule (not yet in effect): boolean-op=and score=INFINITY{" (id: test-rule)" if flag_full else ""}
                        Expression: date gt {self._tomorrow}{" (id: test-rule-expr)" if flag_full else ""}
                """
            ),
        )

    def test_not_yet_in_effect_primitive_plain(self):
        self.assert_not_yet_in_effect_primitive(flag_full=False, flag_all=False)

    def test_not_yet_in_effect_primitive_full(self):
        self.assert_not_yet_in_effect_primitive(flag_full=True, flag_all=False)

    def test_not_yet_in_effect_primitive_all(self):
        self.assert_not_yet_in_effect_primitive(flag_full=False, flag_all=True)

    def test_not_yet_in_effect_primitive_full_all(self):
        self.assert_not_yet_in_effect_primitive(flag_full=True, flag_all=True)

    def test_not_yet_in_effect_group_plain(self):
        self.fixture_group()
        self.assert_pcs_success(
            "constraint location dummy_group rule id=test-rule score=INFINITY".split()
            + [f"date gt {self._tomorrow}"]
        )
        self.assert_pcs_success(
            ["constraint"],
            outdent(
                f"""\
                Location Constraints:
                  resource 'dummy_group'
                    Rules:
                      Rule (not yet in effect): boolean-op=and score=INFINITY
                        Expression: date gt {self._tomorrow}
                """
            ),
        )

    def fixture_complex_primitive(self):
        self.assert_pcs_success(
            "resource create D1 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "resource create D2 ocf:pcsmock:minimal".split()
        )
        self.assert_pcs_success(
            "constraint location D1 rule id=test-rule-D1-1 score=INFINITY".split()
            + ["not_defined pingd"]
        )
        self.assert_pcs_success(
            "constraint location D1 rule id=test-rule-D1-2 score=INFINITY".split()
            + ["(date eq 2019-01-01 or date eq 2019-01-30) and #uname eq node1"]
        )
        self.assert_pcs_success(
            "constraint location D2 rule id=test-constr-D2 score=INFINITY".split()
            + ["date in_range 2019-01-01 to 2019-02-01"]
        )
        self.assert_pcs_success(
            "constraint location D2 rule id=test-duration score=INFINITY".split()
            + ["date in_range 2019-03-01 to duration weeks=2"]
        )

    def test_complex_primitive_plain(self):
        self.fixture_complex_primitive()
        self.assert_pcs_success(
            ["constraint"],
            outdent(
                """\
                Location Constraints:
                  resource 'D1'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Expression: not_defined pingd
                  resource 'D1'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Rule: boolean-op=or
                          Expression: date eq 2019-01-01
                          Expression: date eq 2019-01-30
                        Expression: #uname eq node1
                """
            ),
        )

    def test_complex_primitive_all(self):
        self.fixture_complex_primitive()
        self.assert_pcs_success(
            "constraint --all".split(),
            outdent(
                """\
                Location Constraints:
                  resource 'D1'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Expression: not_defined pingd
                  resource 'D1'
                    Rules:
                      Rule: boolean-op=and score=INFINITY
                        Rule: boolean-op=or
                          Expression: date eq 2019-01-01
                          Expression: date eq 2019-01-30
                        Expression: #uname eq node1
                  resource 'D2'
                    Rules:
                      Rule (expired): boolean-op=and score=INFINITY
                        Expression: date in_range 2019-01-01 to 2019-02-01
                  resource 'D2'
                    Rules:
                      Rule (expired): boolean-op=and score=INFINITY
                        Expression: date in_range 2019-03-01 to duration
                          Duration: weeks=2
                """
            ),
        )


class OrderVsGroup(unittest.TestCase, AssertPcsMixin):
    empty_cib = rc("cib-empty.xml")

    def setUp(self):
        self.temp_cib = get_tmp_file("tier1_constraint_order_vs_group")
        write_file_to_tmpfile(self.empty_cib, self.temp_cib)
        self.pcs_runner = PcsRunner(self.temp_cib.name)
        self.pcs_runner.mock_settings = get_mock_settings()
        self.assert_pcs_success(
            "resource create A ocf:pcsmock:minimal --group grAB".split(),
            stderr_full=DEPRECATED_DASH_DASH_GROUP,
        )
        self.assert_pcs_success(
            "resource create B ocf:pcsmock:minimal --group grAB".split(),
            stderr_full=DEPRECATED_DASH_DASH_GROUP,
        )
        self.assert_pcs_success(
            "resource create C ocf:pcsmock:minimal --group grC".split(),
            stderr_full=DEPRECATED_DASH_DASH_GROUP,
        )
        self.assert_pcs_success("resource create D ocf:pcsmock:minimal".split())

    def tearDown(self):
        self.temp_cib.close()

    def test_deny_resources_in_one_group(self):
        self.assert_pcs_fail(
            "constraint order A then B".split(),
            "Error: Cannot create an order constraint for resources in the same group\n",
        )

    def test_allow_resources_in_different_groups(self):
        self.assert_pcs_success(
            "constraint order A then C".split(),
            stderr_full=(
                "Adding A C (kind: Mandatory) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )

    def test_allow_grouped_and_not_grouped_resource(self):
        self.assert_pcs_success(
            "constraint order A then D".split(),
            stderr_full=(
                "Adding A D (kind: Mandatory) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )

    def test_allow_group_and_resource(self):
        self.assert_pcs_success(
            "constraint order grAB then C".split(),
            stderr_full=(
                "Adding grAB C (kind: Mandatory) (Options: first-action=start "
                "then-action=start)\n"
            ),
        )
