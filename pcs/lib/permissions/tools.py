import logging
from typing import Any, Collection, Optional, cast

from pcs.common import reports
from pcs.common.permissions.types import PermissionAccessType
from pcs.lib.file.instance import FileInstance
from pcs.lib.file.raw_file import RawFileError, raw_file_error_report
from pcs.lib.interface.config import ParserErrorException
from pcs.lib.permissions.config.facade import FacadeV2 as PcsSettingsFacade

from .const import DEFAULT_PERMISSIONS


def complete_access_list(
    access_list: Collection[PermissionAccessType],
) -> set[PermissionAccessType]:
    if PermissionAccessType.FULL in access_list:
        return set(PermissionAccessType)

    permission_set = set(access_list)
    if PermissionAccessType.WRITE in permission_set:
        return permission_set | {PermissionAccessType.READ}
    return permission_set


def read_pcs_settings_conf(
    logger: Optional[logging.Logger] = None,
) -> tuple[PcsSettingsFacade, reports.ReportItemList]:
    def _log(level: int, msg: str, *args: Any) -> None:
        if logger is not None:
            logger.log(level, msg, args)

    file_instance = FileInstance.for_pcs_settings_config()
    report_list: reports.ReportItemList = []

    default_empty_file = PcsSettingsFacade.create(
        # reasonable default if file doesn't exist
        data_version=0,
        permissions=DEFAULT_PERMISSIONS,
    )
    if not file_instance.raw_file.exists():
        _log(
            logging.DEBUG,
            "File '%s' doesn't exist, using default configuration",
            file_instance.raw_file.metadata.path,
        )
        return default_empty_file, report_list

    try:
        return cast(
            PcsSettingsFacade, file_instance.read_to_facade()
        ), report_list
    except RawFileError as e:
        report = raw_file_error_report(e)
        _log(logging.ERROR, report.message.message)
        report_list.append(report)
    except ParserErrorException as e:
        error_reports = file_instance.parser_exception_to_report_list(e)
        for report in error_reports:
            _log(logging.ERROR, report.message.message)
        report_list.extend(error_reports)
    return PcsSettingsFacade.create(), report_list
