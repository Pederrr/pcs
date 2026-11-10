import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from pcs.cli.common import lib_wrapper
from pcs.common.reports import ReportItemList, ReportProcessor


@dataclass(frozen=True)
class ActualCall:
    """
    A call made by the CLI code to the mocked library

    path -- attribute path of the call, e.g. ("tag", "create") for
        lib.tag.create(...)
    """

    path: tuple[str, ...]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]

    @property
    def name(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True, kw_only=True)
class CallSpec(ABC):
    """
    Base class for specifications of expected library calls

    output -- value returned from the call
    raises -- exception raised from the call
    reports -- reports sent to the report processor before returning / raising
    """

    output: Any = None
    raises: Exception | None = None
    reports: ReportItemList = field(default_factory=list)

    @abstractmethod
    def describe(self) -> str:
        """
        Return a human readable description of the expected call
        """

    @abstractmethod
    def verify(self, actual: ActualCall, index: int, all_calls: str) -> None:
        """
        Raise AssertionError if the actual call doesn't match this spec

        actual -- the call made by the CLI code
        index -- 1-based position of this spec in the list of expected calls
        all_calls -- pre-rendered listing of all expected calls
        """

    def respond(self, report_processor: ReportProcessor) -> Any:
        """
        Simulate the outcome of the call
        """
        if self.reports:
            report_processor.report_list(self.reports)
        if self.raises is not None:
            raise self.raises
        return self.output


@dataclass(frozen=True)
class LibCallSpec(CallSpec):
    """
    Expected call of a local library command, e.g. lib.tag.create(...)

    method -- library command in 'module.method' format
    """

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.method}({format_call_args(self.args, self.kwargs)})"

    def verify(self, actual: ActualCall, index: int, all_calls: str) -> None:
        if actual.name != self.method:
            raise AssertionError(
                f"Library call #{index}: expected "
                f"'{self.method}' but got '{actual.name}'\n"
                f"  Expected args: "
                f"{format_call_args(self.args, self.kwargs)}\n"
                f"  Actual args:   "
                f"{format_call_args(actual.args, actual.kwargs)}\n"
                f"{all_calls}"
            )

        # Translate positional and keyword arguments into keyword arguments
        # using the real library function signature. This helps to determine if
        # the signature of function is correctly called.
        func = _resolve_lib_function(self.method)
        try:
            expected_kwargs = _bind_to_kwargs(func, self.args, self.kwargs)
        except TypeError as e:
            raise AssertionError(
                f"Expected library call #{index} '{self.method}' "
                f"does not match the command signature: {e}"
            ) from e
        try:
            actual_kwargs = _bind_to_kwargs(func, actual.args, actual.kwargs)
        except TypeError as e:
            raise AssertionError(
                f"Actual library call #{index} '{self.method}' "
                f"does not match the command signature: {e}"
            ) from e

        if actual_kwargs != expected_kwargs:
            raise AssertionError(
                f"Library call #{index} '{self.method}': "
                f"args mismatch\n"
                f"  Expected: "
                f"{format_call_args(self.args, self.kwargs)}\n"
                f"  Actual:   "
                f"{format_call_args(actual.args, actual.kwargs)}\n"
                f"{all_calls}"
            )


def format_call_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    parts = [repr(a) for a in args]
    parts.extend(f"{k}={v!r}" for k, v in kwargs.items())
    return ", ".join(parts)


def _resolve_lib_function(method: str) -> Any:
    module_name, _, method_name = method.partition(".")
    if not module_name or not method_name or "." in method_name:
        raise AssertionError(
            f"Library call name '{method}' is not in 'module.method' format"
        )
    # load_module wires real library functions through bind(). Making bind
    # return the raw function gives us a namedtuple mapping exposed names to the
    # actual library functions instead of their bound wrappers.
    with mock.patch.object(
        lib_wrapper, "bind", lambda env, run_with_middleware, fn: fn
    ):
        try:
            module = lib_wrapper.load_module(
                mock.Mock(), mock.Mock(), module_name
            )
        except Exception as e:
            raise AssertionError(
                f"Cannot resolve library module '{module_name}' "
                f"for call '{method}': {e}"
            ) from e
    try:
        return getattr(module, method_name)
    except AttributeError as e:
        raise AssertionError(
            f"Library module '{module_name}' has no command '{method_name}' "
            f"for call '{method}': {e}"
        ) from e


def _bind_to_kwargs(
    func: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    signature = inspect.signature(func)
    # Drop the first parameter (env), which the CLI never passes.
    params = list(signature.parameters.values())[1:]
    bound = signature.replace(parameters=params).bind(*args, **kwargs)
    return dict(bound.arguments)
