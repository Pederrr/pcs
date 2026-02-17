from pcs.lib.auth.const import ADMIN_GROUP
from pcs.lib.permissions.config.types import (
    PermissionAccessType,
    PermissionEntry,
    PermissionTargetType,
)

DEFAULT_PERMISSIONS = (
    # reasonable default if file doesn't exist
    # set default permissions for backwards compatibility (there is
    # no way to differentiante between an old cluster without config
    # and a new cluster without config)
    # Since ADMIN_GROUP has access to pacemaker by default anyway, we can safely
    # allow access in pcsd as well even for new clusters.
    PermissionEntry(
        name=ADMIN_GROUP,
        type=PermissionTargetType.GROUP,
        allow=(
            PermissionAccessType.READ,
            PermissionAccessType.WRITE,
            PermissionAccessType.GRANT,
        ),
    ),
)
