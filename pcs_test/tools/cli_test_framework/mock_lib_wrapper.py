from typing import Any

from pcs.cli.reports.processor import ReportProcessorToConsole

from pcs_test.tools.cli_test_framework.call_specs import (
    ActualCall,
    CallSpec,
    format_call_args,
)


class _CallRecorder:
    """
    Collects an attribute path and forwards the final call to MockLibrary
    """

    def __init__(self, mock_lib: "MockLibrary", path: tuple[str, ...]) -> None:
        self._mock_lib = mock_lib
        self._path = path

    def __getattr__(self, name: str) -> "_CallRecorder":
        return _CallRecorder(self._mock_lib, (*self._path, name))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._mock_lib.on_call(ActualCall(self._path, args, kwargs))


class MockLibrary:
    def __init__(self, calls: list[CallSpec]) -> None:
        self._calls = calls
        self._index = 0
        self._report_processor = ReportProcessorToConsole(debug=False)

    def __getattr__(self, name: str) -> _CallRecorder:
        return _CallRecorder(self, (name,))

    def on_call(self, actual: ActualCall) -> Any:
        if self._index >= len(self._calls):
            raise AssertionError(
                f"Unexpected library call: {actual.name}("
                f"{format_call_args(actual.args, actual.kwargs)})\n"
                f"All {len(self._calls)} expected call(s) were already "
                f"consumed.\n{self._format_all_calls()}"
            )

        expected = self._calls[self._index]
        self._index += 1
        expected.verify(actual, self._index, self._format_all_calls())
        return expected.respond(self._report_processor)

    def assert_exhausted(self) -> None:
        remaining = self._calls[self._index :]
        if remaining:
            raise AssertionError(
                f"{len(remaining)} expected library call(s) were not made:\n"
                + "\n".join(
                    f"  {i}. {call.describe()}"
                    for i, call in enumerate(remaining, self._index + 1)
                )
            )

    def _format_all_calls(self) -> str:
        lines = [f"All expected calls (current index={self._index}):"]
        for i, call in enumerate(self._calls, 1):
            marker = " >>>" if i == self._index else "    "
            lines.append(f"{marker}{i}. {call.describe()}")
        return "\n".join(lines)
