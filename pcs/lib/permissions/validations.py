from typing import TYPE_CHECKING, Sequence

from pcs.common import reports
from pcs.common.permissions.dto import PermissionEntryDto

if TYPE_CHECKING:
    from pcs.lib.permissions.config.types import PermissionTargetType


def validate_set_permissions(
    permissions: Sequence[PermissionEntryDto],
) -> reports.ReportItemList:
    report_list: reports.ReportItemList = []
    user_set: set[tuple[str, PermissionTargetType]] = set()
    duplicate_set: set[tuple[str, PermissionTargetType]] = set()
    for perm in permissions:
        if not perm.name:
            report_list.append(
                reports.ReportItem.error(
                    reports.messages.InvalidOptionValue(
                        "name", "", allowed_values=None, cannot_be_empty=True
                    )
                )
            )

        if (perm.name, perm.type) in user_set:
            duplicate_set.add((perm.name, perm.type))
        user_set.add((perm.name, perm.type))
    if duplicate_set:
        report_list.append(
            reports.ReportItem.error(
                reports.messages.PermissionDuplication(sorted(duplicate_set))
            )
        )
    return report_list
