from collections.abc import Mapping
from dataclasses import dataclass

from ekb.errors import KBError


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    workspaces: Mapping[str, str]
    audiences: tuple[str, ...] = ("all",)

    def require(self, workspace: str, edit: bool = False) -> None:
        role = self.workspaces.get(workspace)
        if role not in ("reader", "editor") or (edit and role != "editor"):
            raise KBError("KB.FORBIDDEN", 403)
