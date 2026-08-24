"""Checks on the test suite itself.

A test that does not run is worse than a missing one: the count goes up, the
report is green, and the thing it was written to protect is unguarded.
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).parent


def _decorated_tests():
    """Every `test_*` function that carries a decorator, with its decorators."""
    for path in sorted(TESTS.glob('test_*.py')):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith('test_'):
                continue
            for dec in node.decorator_list:
                yield path.name, node.name, node.lineno, ast.unparse(dec)


def test_no_test_is_declared_as_a_fixture():
    """`@pytest.fixture` on a `test_*` function makes pytest collect it as a
    fixture instead of a test, and nothing anywhere says so.

    This is not hypothetical. Removing the scanner deleted two fixtures and
    left their decorators attached to the tests that followed them, so
    `test_a_template_with_no_contents_is_refused` and
    `test_home_leads_with_the_effort_weighted_percentage` silently stopped
    running. Both still passed once reconnected — the assertions were fine, the
    wiring was not, which is exactly the failure this file exists to catch.
    """
    bad = [f'{f}:{line} {name} is decorated {dec}'
           for f, name, line, dec in _decorated_tests()
           if dec in ('pytest.fixture', 'fixture')
           or dec.startswith(('pytest.fixture(', 'fixture('))]

    assert not bad, 'these are collected as fixtures, so they never run:\n' + '\n'.join(bad)
