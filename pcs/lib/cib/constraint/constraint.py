from typing import (
    Callable,
    cast,
)

from lxml.etree import (
    SubElement,
    _Element,
)

from pcs.common import reports
from pcs.lib.cib.constraint import resource_set
from pcs.lib.cib.tools import (
    ElementNotFound,
    find_unique_id,
    get_element_by_id,
)
from pcs.lib.errors import LibraryError
from pcs.lib.xml_tools import get_root

from .common import validate_constrainable_elements


# DEPRECATED
def _validate_attrib_names(attrib_names, options):
    invalid_names = [name for name in options if name not in attrib_names]
    if invalid_names:
        raise LibraryError(
            reports.ReportItem.error(
                reports.messages.InvalidOptions(
                    sorted(invalid_names), sorted(attrib_names), None
                )
            )
        )


# DEPRECATED, use pcs.lib.cib.constraint.common.validate_constrainable_elements
def find_valid_resource_id(
    report_processor: reports.ReportProcessor, cib, in_clone_allowed, _id
):
    try:
        report_processor.report_list(
            validate_constrainable_elements(
                [get_element_by_id(cib, _id)], in_clone_allowed
            )
        )
    except ElementNotFound:
        report_processor.report(
            reports.ReportItem.error(reports.messages.IdNotFound(_id, []))
        )
    if report_processor.has_errors:
        raise LibraryError()
    return _id


# DEPRECATED
def prepare_options(attrib_names, options, create_id_fn, validate_id):
    _validate_attrib_names(attrib_names + ("id",), options)
    options = options.copy()

    if "id" not in options:
        options["id"] = create_id_fn()
    else:
        validate_id(options["id"])
    return options


# DEPRECATED, use pcs.lib.cib.constraint.common._create_set_constraint_id
def create_id(cib, type_prefix, resource_set_list):
    # Create a semi-random id. We need it to be predictable (for testing), short
    # and somehow different than other ids so that we don't spend much time in
    # find_unique_id.
    # Avoid using actual resource names. It makes the id very long (consider 10
    # or more resources in a set constraint). Also, if a resource is deleted
    # and therefore removed from the constraint, the id no longer matches the
    # constraint.
    resource_ids = []
    for _set in resource_set_list:
        resource_ids.extend(_set["ids"])
    id_part = "".join([_id[0] + _id[-1] for _id in resource_ids][:3])
    return find_unique_id(cib, f"{type_prefix}_set_{id_part}")


# DEPRECATED, replace with pcs.lib.cib.constraint.common.DuplicatesChecker
def have_duplicate_resource_sets(element, other_element):
    def get_id_set_list(element):
        return [
            resource_set.get_resource_id_set_list(resource_set_item)
            for resource_set_item in element.findall(".//resource_set")
        ]

    return get_id_set_list(element) == get_id_set_list(other_element)


# DEPRECATED, replace with pcs.lib.cib.constraints.duplicates
def check_is_without_duplication(
    report_processor: reports.ReportProcessor,
    constraint_section: _Element,
    element: _Element,
    are_duplicate: Callable[[_Element, _Element], bool],
    duplication_allowed: bool = False,
) -> None:
    duplicate_element_list = [
        duplicate_element
        for duplicate_element in cast(
            # The xpath method has a complicated return value, but we know our
            # xpath expression returns only elements.
            list[_Element],
            constraint_section.xpath(
                ".//*[local-name()=$tag_name]", tag_name=element.tag
            ),
        )
        if (
            element is not duplicate_element
            and are_duplicate(element, duplicate_element)
        )
    ]
    if not duplicate_element_list:
        return

    if report_processor.report_list(
        [
            reports.ReportItem(
                severity=reports.item.get_severity(
                    reports.codes.FORCE,
                    duplication_allowed,
                ),
                message=reports.messages.DuplicateConstraintsExist(
                    [
                        str(duplicate.attrib["id"])
                        for duplicate in duplicate_element_list
                    ]
                ),
            ),
        ]
    ).has_errors:
        raise LibraryError()


# DEPRECATED use pcs.lib.cib.constraint.common.create_constraint_with_set
def create_with_set(constraint_section, tag_name, options, resource_set_list):
    if not resource_set_list:
        raise LibraryError(
            reports.ReportItem.error(reports.messages.EmptyResourceSetList())
        )
    element = SubElement(constraint_section, tag_name)
    element.attrib.update(options)
    if tag_name == "rsc_order":
        all_resource_ids = []
        for resource_set_item in resource_set_list:
            all_resource_ids.extend(resource_set_item["ids"])
        resource_set.is_resource_in_same_group(
            get_root(constraint_section), all_resource_ids
        )
    for resource_set_item in resource_set_list:
        resource_set.create_old(element, resource_set_item)
    return element
