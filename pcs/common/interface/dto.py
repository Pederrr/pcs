from dataclasses import asdict, fields, is_dataclass
from enum import Enum, EnumType
from types import NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    NewType,
    TypeVar,
    Union,
    get_type_hints,
)
from typing import get_args as get_type_args
from typing import get_origin as get_type_origin

import dacite

import pcs.common.async_tasks.types as async_tasks_types
import pcs.common.permissions.types as permissions_types
from pcs.common import types
from pcs.common.str_tools import format_list

if TYPE_CHECKING:
    from _typeshed import DataclassInstance  # pylint: disable=import-error
else:

    class DataclassInstance:
        pass


PrimitiveType = Union[str, int, float, bool, None]
DtoPayload = dict[str, "SerializableType"]
SerializableType = Union[
    PrimitiveType, DtoPayload, Iterable["SerializableType"]
]

T = TypeVar("T")
E = TypeVar("E", bound=Enum)

ToDictMetaKey = NewType("ToDictMetaKey", str)
META_NAME = ToDictMetaKey("META_NAME")


class PayloadConversionError(Exception):
    pass


class _UnionNotAllowed(Exception):
    pass


class DataTransferObject(DataclassInstance):
    pass


def _safe_enum_cast(enum_class: type[E]) -> Callable[[Any], E]:
    def _cast_value(value: Any) -> E:
        try:
            return enum_class(value)
        except ValueError as e:
            valid_values = format_list([f.value for f in enum_class])
            raise PayloadConversionError(
                f"Invalid value '{value}' for Enum '{enum_class.__name__}', "
                f"expected one of {valid_values}"
            ) from e

    return _cast_value


DTO_TYPE_HOOKS_MAP: dict[type[Any], Callable[[Any], Any]] = {
    # Dacite does not convert Enums automatically. We previously used simple
    # casting: https://github.com/konradhalas/dacite#casting, but that is not
    # sufficient, since it does not do proper handling of values that cannot
    # be converted to enums.
    #
    # All enum types used in lib command parameters must be listed here!
    **{
        enum_type: _safe_enum_cast(enum_type)
        for enum_type in [
            types.CibRuleExpressionType,
            types.CibRuleInEffectStatus,
            types.CorosyncNodeAddressType,
            types.CorosyncTransportType,
            types.DrRole,
            types.ResourceRelationType,
            async_tasks_types.TaskFinishType,
            async_tasks_types.TaskState,
            async_tasks_types.TaskKillReason,
            permissions_types.PermissionGrantedType,
            permissions_types.PermissionTargetType,
        ]
    },
    #
    # JSON does not support tuples, only lists. However, tuples are
    # used e.g. to express fixed-length structures. If a tuple is
    # expected and a list is provided, we convert it to a tuple.
    # Unfortunately, we cannot apply this rule generically to all
    # tuples, so we must handle specific cases manually.
    #
    # Covered cases:
    # * acl.create_role:
    #   permission_info_list: list[tuple[str, str, str]]
    tuple[str, str, str]: lambda v: tuple(v) if isinstance(v, list) else v,
    # Covered cases:
    # * resource.get_cibsecrets:
    #   queries: Sequence[tuple[str, str]]
    tuple[str, str]: lambda v: tuple(v) if isinstance(v, list) else v,
}


def meta(name: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if name:
        metadata[META_NAME] = name
    return metadata


_PRIMITIVE_TYPES = frozenset({str, int, float, bool, NoneType})


# _type is Any - in reality, it is either one of:
# * type
# * enum.EnumType
# * something defined in typing module, e.g. typing._GenericAlias, typing.Union
# Especially the typing module changes with new Python versions.
# Properly typing (rather metatyping, since its input and output are types)
# this function doesn't bring any benefits.
def _resolve_type_from_union(_type: Any) -> Any:
    # Dataclass fields may be typed as 'Optional[some_type]' or
    # 'Union[some_type, None]' or 'some_type | None'. This function extracts
    # the inner type from an Optional, and thus allows to properly detect types
    # of such dataclass fields.
    #
    # For unions of only primitive types (e.g. Union[str, int, None]),
    # no conversion is needed regardless of the actual runtime value, so the
    # union type itself is returned and the caller passes the value through.
    #
    # It raises an exception if a Union contains more than one non-primitive
    # type, because in that case the correct transformation cannot be
    # determined. Such a field should never be defined in a dataclass.

    # Internal representation of Union and Optional is different in Python 3.12
    # and 3.14. To be able to handle the differences, typing.get_origin is
    # used. It transforms all the representations to Union or UnionType.
    # https://docs.python.org/3/library/typing.html#typing.Union
    _type_origin = get_type_origin(_type)
    if not (_type_origin is Union or _type_origin is UnionType):
        return _type

    inner_types_without_none = [
        inner_type
        for inner_type in get_type_args(_type)
        if inner_type is not NoneType
    ]
    if len(inner_types_without_none) == 1:
        return inner_types_without_none[0]
    if all(t in _PRIMITIVE_TYPES for t in inner_types_without_none):
        return _type
    raise _UnionNotAllowed()


# _type is Any - in reality, it is either one of:
# * type
# * enum.EnumType
# * something defined in typing module, e.g. typing._GenericAlias, typing.Union
# Especially the typing module changes with new Python versions.
# Properly typing (rather metatyping, since its input and output are types)
# this function doesn't bring any benefits.
def _is_compatible_type(_type: Any, arg_index: int) -> bool:
    return (
        hasattr(_type, "__args__")
        and len(_type.__args__) >= arg_index
        and is_dataclass(_type.__args__[arg_index])
    )


# _type is Any - in reality, it is either one of:
# * type
# * enum.EnumType
# * something defined in typing module, e.g. typing._GenericAlias, typing.Union
# Especially the typing module changes with new Python versions.
# Properly typing (rather metatyping, since its input and output are types)
# this function doesn't bring any benefits.
def _is_enum_type(_type: Any, arg_index: int) -> bool:
    return (
        hasattr(_type, "__args__")
        and len(_type.__args__) >= arg_index
        and type(_type.__args__[arg_index]) is EnumType
    )


# returns Any as the type of enum value can be anything and it can be different
# for each Enum
def _convert_enum(value: Enum) -> Any:
    return value.value


# TODO: the orignal point of this loop was to rename
# the keys according to the META_NAME metadata
#
# META_NAME renaming is however used only on one place: ResourceAgentActionDto
# which is part of return value for resource_agent.get_agent_metadata
# - this command is used from cli, but to_dict is not used to print the data
#       - the actions are not even printed
# - does not seem to be used from WebUI
# - does not even work from APIv1/2, because APIv2 TaskResultDto has
#   command result type typed as Any, so this whole function does nothing
#
# The current enum conversion does not work correctly for APIv2/1, becuase of
# the Any in TaskResultDto
#
# Do we really need this overly complicated parostroj?
#
# Without META_NAME conversion, we would only need to:
# - use dataclasses.asdict to convert into dict, which already works recursively
# - go through all values recursively, and replace the enum objects with values
def _convert_dict(
    klass: type[DataTransferObject], obj_dict: DtoPayload
) -> DtoPayload:
    new_dict = {}
    # resolve forward references in type hints, because type-detecting
    # functions do not work with forward references
    type_hints = get_type_hints(klass)
    for _field in fields(klass):
        try:
            _type = _resolve_type_from_union(type_hints[_field.name])
        except _UnionNotAllowed as e:
            raise AssertionError(
                f"Field '{_field.name}' in class '{klass}' is a Union: "
                f"{_field.type}. "
                "Dataclass fields cannot be Unions unless they are Optional "
                "(a Union of one type and None) or a union of only primitive "
                "types."
            ) from e
        value = obj_dict[_field.name]

        new_value: SerializableType
        if value is None:
            # None must be handled here, other checks fail if they get None
            new_value = value
        elif is_dataclass(_type):
            new_value = _convert_dict(_type, value)  # type: ignore
        elif isinstance(value, list) and _is_compatible_type(_type, 0):
            new_value = [
                _convert_dict(_type.__args__[0], item) for item in value
            ]
        elif isinstance(value, list) and _is_enum_type(_type, 0):
            new_value = [_convert_enum(item) for item in value]
        elif isinstance(value, dict) and _is_compatible_type(_type, 1):
            new_value = {
                item_key: _convert_dict(_type.__args__[1], item_val)  # type: ignore[arg-type]
                for item_key, item_val in value.items()
            }
        elif isinstance(value, Enum):
            new_value = _convert_enum(value)
        else:
            new_value = value
        new_dict[_field.metadata.get(META_NAME, _field.name)] = new_value
    return new_dict


def to_dict(obj: DataTransferObject) -> DtoPayload:
    return _convert_dict(obj.__class__, asdict(obj))


DTOTYPE = TypeVar("DTOTYPE", bound=DataTransferObject)


# TODO:
# the whole purpose of this is to be able to change the names of fields
# according to the META_NAME
#
# but meta name is used only in one place: ResourceAgentActionDto
# - which is not used as an input for any command, so this meta translation
#   is not even used on the `from_dict` side
#
# Do we really need this overly complicated parostroj?
def _convert_payload(klass: type[DTOTYPE], data: DtoPayload) -> DtoPayload:
    try:
        new_dict = dict(data)
    except ValueError as e:
        raise PayloadConversionError() from e
    # resolve forward references in type hints, because type-detecting
    # functions do not work with forward references
    type_hints = get_type_hints(klass)
    for _field in fields(klass):
        new_name = _field.metadata.get(META_NAME, _field.name)
        if new_name not in data:
            continue

        try:
            _type = _resolve_type_from_union(type_hints[_field.name])
        except _UnionNotAllowed as e:
            raise AssertionError(
                f"Field '{_field.name}' in class '{klass}' is a Union: "
                f"{_field.type}. "
                "Dataclass fields cannot be Unions unless they are Optional "
                "(a Union of one type and None) or a union of only primitive "
                "types."
            ) from e
        value = data[new_name]

        new_value: SerializableType
        if value is None:
            # None must be handled here, other checks fail if they get None
            new_value = value
        elif is_dataclass(_type):
            new_value = _convert_payload(_type, value)  # type: ignore
        elif isinstance(value, list) and _is_compatible_type(_type, 0):
            new_value = [
                _convert_payload(_type.__args__[0], item) for item in value
            ]
        elif isinstance(value, dict) and _is_compatible_type(_type, 1):
            new_value = {
                item_key: _convert_payload(_type.__args__[1], item_val)  # type: ignore[arg-type]
                for item_key, item_val in value.items()
            }
        else:
            new_value = value
        del new_dict[new_name]
        new_dict[_field.name] = new_value
    return new_dict


def from_dict(
    cls: type[DTOTYPE], data: DtoPayload, strict: bool = False
) -> DTOTYPE:
    return dacite.from_dict(
        data_class=cls,
        data=_convert_payload(cls, data),
        config=dacite.Config(
            type_hooks=DTO_TYPE_HOOKS_MAP,
            strict=strict,
        ),
    )


class ImplementsToDto:
    def to_dto(self) -> Any:
        raise NotImplementedError()


class ImplementsFromDto:
    @classmethod
    def from_dto(cls: type[T], dto_obj: Any) -> T:
        raise NotImplementedError()
