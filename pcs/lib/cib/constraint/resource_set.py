from typing import Mapping

from lxml.etree import SubElement, _Element

from pcs.common import const, pacemaker, reports
from pcs.common.const import PcmkAction
from pcs.common.pacemaker.constraint import CibResourceSetDto
from pcs.common.pacemaker.role import (
    get_value_for_cib as get_role_value_for_cib,
)
from pcs.common.pacemaker.types import (
    CibResourceSetOrdering,
    CibResourceSetOrderType,
)
from pcs.common.types import StringIterable
from pcs.lib import validate
from pcs.lib.cib.const import TAG_RESOURCE_REF, TAG_RESOURCE_SET
from pcs.lib.cib.resource import group
from pcs.lib.cib.resource.common import get_parent_resource
from pcs.lib.cib.tools import (
    IdProvider,
    Version,
    are_new_role_names_supported,
    find_unique_id,
    get_elements_by_ids,
    role_constructor,
)
from pcs.lib.errors import LibraryError
from pcs.lib.pacemaker.values import is_true
from pcs.lib.tools import get_optional_value

_ATTRIBUTES = ("action", "require-all", "role", "sequential")


# DEPRECATED
def prepare_set(
    find_valid_id, resource_set, report_processor: reports.ReportProcessor
):
    """return resource_set with corrected ids"""
    if report_processor.report_list(
        _validate_options(resource_set["options"])
    ).has_errors:
        raise LibraryError()
    return {
        "ids": [find_valid_id(id_) for id_ in resource_set["ids"]],
        "options": resource_set["options"],
    }


# DEPRECATED pacemaker cares, thus the validation should be constraint specific
def _validate_options(options) -> reports.ReportItemList:
    # Pacemaker does not care currently about meaningfulness for concrete
    # constraint, so we use all attribs.
    validators = [
        validate.NamesIn(_ATTRIBUTES, option_type="set"),
        validate.ValueIn("action", const.PCMK_ACTIONS),
        validate.ValuePcmkBoolean("require-all"),
        validate.ValueIn("role", const.PCMK_ROLES),
        validate.ValuePcmkBoolean("sequential"),
    ]
    return validate.ValidatorAll(validators).validate(options)


def create(
    parent_el: _Element,
    id_provider: IdProvider,
    cib_schema_version: Version,
    rsc_list: StringIterable,
    set_options: Mapping[str, str],
) -> _Element:
    """
    Create a new resource set element

    parent_el -- where to create the set
    id_provider -- elements' ids generator
    cib_schema_version -- current CIB schema version
    rsc_list -- list of resources in the set
    set_options -- set attributes
    """
    rsc_set_el = SubElement(parent_el, TAG_RESOURCE_SET)

    if "id" not in set_options:
        rsc_set_el.attrib["id"] = id_provider.allocate_id(
            "{0}_set".format(parent_el.attrib.get("id", "constraint_set")),
        )

    for name, value in set_options.items():
        if name == "role":
            # noqa - for loop variable 'value' overwritten by assignment target
            value = get_role_value_for_cib(  # noqa: PLW2901
                const.PcmkRoleType(value),
                cib_schema_version >= const.PCMK_NEW_ROLES_CIB_VERSION,
            )
        if value != "":
            rsc_set_el.attrib[name] = value

    for rsc_id in rsc_list:
        SubElement(rsc_set_el, TAG_RESOURCE_REF).attrib["id"] = rsc_id

    return rsc_set_el


# DEPRECATED
def create_old(parent, resource_set):
    """
    parent - lxml element for append new resource_set
    """
    element = SubElement(parent, "resource_set")
    if "role" in resource_set["options"]:
        resource_set["options"]["role"] = pacemaker.role.get_value_for_cib(
            resource_set["options"]["role"],
            is_latest_supported=are_new_role_names_supported(parent),
        )
    element.attrib.update(resource_set["options"])
    element.attrib["id"] = find_unique_id(
        parent.getroottree(),
        "{0}_set".format(parent.attrib.get("id", "constraint_set")),
    )

    for _id in resource_set["ids"]:
        SubElement(element, "resource_ref").attrib["id"] = _id

    return element


def get_resource_id_set_list(element: _Element) -> list[str]:
    return [
        str(resource_ref_element.attrib["id"])
        for resource_ref_element in element.findall(f".//{TAG_RESOURCE_REF}")
    ]


def is_resource_in_same_group(cib, resource_id_list):
    # We don't care about not found elements here, that is a job of another
    # validator. We do not care if the id doesn't belong to a resource either
    # for the same reason.
    element_list, _ = get_elements_by_ids(cib, set(resource_id_list))

    parent_list = []
    for element in element_list:
        parent = get_parent_resource(element)
        if parent is not None and group.is_group(parent):
            parent_list.append(parent)

    if len(set(parent_list)) != len(parent_list):
        raise LibraryError(
            reports.ReportItem.error(
                reports.messages.CannotSetOrderConstraintsForResourcesInTheSameGroup()
            )
        )


def _resource_set_element_to_dto(
    resource_set_el: _Element,
) -> CibResourceSetDto:
    return CibResourceSetDto(
        set_id=resource_set_el.get("id", ""),
        sequential=get_optional_value(
            is_true, resource_set_el.get("sequential")
        ),
        require_all=get_optional_value(
            is_true, resource_set_el.get("require-all")
        ),
        ordering=get_optional_value(
            CibResourceSetOrdering, resource_set_el.get("ordering")
        ),
        action=get_optional_value(PcmkAction, resource_set_el.get("action")),
        role=get_optional_value(role_constructor, resource_set_el.get("role")),
        score=resource_set_el.get("score"),
        kind=get_optional_value(
            CibResourceSetOrderType, resource_set_el.get("kind")
        ),
        resources_ids=[
            str(rsc_ref.attrib["id"])
            for rsc_ref in resource_set_el.findall(f"./{TAG_RESOURCE_REF}")
        ],
    )


def constraint_element_to_resource_set_dto_list(
    constraint_el: _Element,
) -> list[CibResourceSetDto]:
    return [
        _resource_set_element_to_dto(set_el)
        for set_el in constraint_el.findall(f"./{TAG_RESOURCE_SET}")
    ]
