import inspect
from dataclasses import (
    fields,
    is_dataclass,
)
from enum import EnumType
from types import NoneType, UnionType
from typing import (
    Container,
    Iterable,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from unittest import TestCase

from pcs.common.interface.dto import DTO_TYPE_HOOKS_MAP
from pcs.daemon.async_tasks.worker.command_mapping import COMMAND_MAP

_PRIMITIVE_TYPES = frozenset({str, int, float, bool, NoneType})


def prohibited_types_used(_type, prohibited_types):
    if _type in prohibited_types:
        return True
    generic = get_origin(_type)
    if generic:
        if generic in prohibited_types:
            return True
        return any(
            prohibited_types_used(arg, prohibited_types)
            for arg in get_args(_type)
        )
    if is_dataclass(_type):
        # resolve forward references in type hints, because type-detecting
        # functions do not work with forward references
        type_hints = get_type_hints(_type)
        return any(
            prohibited_types_used(type_hints[field.name], prohibited_types)
            for field in fields(_type)
        )
    return False


def _get_generic(annotation):
    return getattr(annotation, "__origin__", None)


def _find_disallowed_types(_type, allowed_types, _seen=None):
    if _seen is None:
        _seen = set()
    type_id = id(_type)
    if type_id in _seen:
        return set()
    _seen.add(type_id)

    disallowed = set()
    generic = get_origin(_type)

    if generic is None:
        if isinstance(_type, EnumType) and _type not in allowed_types:
            disallowed.add(_type)
    else:
        # tuples apart from tuple with ellipsis must have explicit type hooks
        if (
            generic is tuple
            and Ellipsis not in get_args(_type)
            and _type not in allowed_types
        ):
            disallowed.add(_type)
        for arg in get_args(_type):
            disallowed.update(_find_disallowed_types(arg, allowed_types, _seen))

    if is_dataclass(_type):
        # resolve forward references in type hints, because type-detecting
        # functions do not work with forward references
        type_hints = get_type_hints(_type)
        for field in fields(_type):
            disallowed.update(
                _find_disallowed_types(
                    type_hints[field.name], allowed_types, _seen
                )
            )
    return disallowed


def _find_disallowed_unions(_type, _seen=None):
    if _seen is None:
        _seen = set()
    type_id = id(_type)
    if type_id in _seen:
        return set()
    _seen.add(type_id)

    disallowed = set()
    origin = get_origin(_type)

    if origin is Union or origin is UnionType:
        args = get_args(_type)
        non_none = [a for a in args if a is not NoneType]
        if len(non_none) == 1:
            disallowed.update(_find_disallowed_unions(non_none[0], _seen))
        elif not all(t in _PRIMITIVE_TYPES for t in non_none):
            disallowed.add(_type)
            for arg in args:
                disallowed.update(_find_disallowed_unions(arg, _seen))
    elif origin is not None:
        for arg in get_args(_type):
            disallowed.update(_find_disallowed_unions(arg, _seen))

    if is_dataclass(_type):
        type_hints = get_type_hints(_type)
        for field in fields(_type):
            disallowed.update(
                _find_disallowed_unions(type_hints[field.name], _seen)
            )
    return disallowed


class DaciteTypingCompatibilityTest(TestCase):
    def test_all(self):
        prohibited_types = (Iterable, Container)
        prohibited_types_normalized = [
            get_origin(_type) for _type in prohibited_types
        ]
        for cmd_name, cmd in COMMAND_MAP.items():
            for param in list(inspect.signature(cmd.cmd).parameters.values())[
                1:
            ]:
                if param.annotation != inspect.Parameter.empty:
                    self.assertFalse(
                        prohibited_types_used(
                            param.annotation, prohibited_types_normalized
                        ),
                        f"Prohibited type used in command: {cmd_name}; argument: {param}; prohibited_types: {prohibited_types}",
                    )

    def test_check_type_hooks_map_types_in_commands(self):
        allowed_types = set(DTO_TYPE_HOOKS_MAP.keys())
        for cmd_name, cmd in COMMAND_MAP.items():
            for param in list(inspect.signature(cmd.cmd).parameters.values())[
                1:
            ]:
                if param.annotation == inspect.Parameter.empty:
                    continue
                disallowed = _find_disallowed_types(
                    param.annotation, allowed_types
                )
                self.assertFalse(
                    disallowed,
                    f"Type(s) {disallowed} in "
                    f"command: {cmd_name}; "
                    f"argument: {param}; "
                    f"not covered by DTO_TYPE_HOOKS_MAP.keys(): "
                    f"{allowed_types}. "
                    "Add the missing type(s) to DTO_TYPE_HOOKS_MAP "
                    "and update FromDictConversion tests in "
                    "test_dto.py accordingly.",
                )

    def test_check_no_disallowed_unions_in_commands(self):
        for cmd_name, cmd in COMMAND_MAP.items():
            for param in list(inspect.signature(cmd.cmd).parameters.values())[
                1:
            ]:
                if param.annotation == inspect.Parameter.empty:
                    continue
                disallowed = _find_disallowed_unions(param.annotation)
                self.assertFalse(
                    disallowed,
                    f"Disallowed Union type(s) {disallowed} found in "
                    f"command: {cmd_name}; argument: {param}. "
                    "Unions must be Optional[T] or a union of only primitive "
                    "types (str, int, float, bool, None).",
                )
