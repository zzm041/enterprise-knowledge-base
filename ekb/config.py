import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ekb.auth import Principal
from ekb.errors import KBError
from ekb.providers import ModelHTTP

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    database: Path
    credentials: dict[str, Principal]
    embedding: dict = field(default_factory=dict)
    generation: dict = field(default_factory=dict)
    reranking: dict = field(default_factory=dict)

    def authenticate(self, token):
        found = self.credentials.get(hashlib.sha256(token.encode()).hexdigest())
        if found is None:
            raise KBError("KB.UNAUTHENTICATED", 401)
        return found

    @staticmethod
    def provider(settings):
        return ModelHTTP(**settings) if settings else None


def load_config(path=None):
    path = Path(
        path or os.environ.get("EKB_CONFIG", ROOT / "runtime" / "config.json")
    ).resolve()
    if not path.is_relative_to(ROOT):
        raise KBError("KB.CONFIG_OUTSIDE_PROJECT", 422)
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        db = (ROOT / data["database"]).resolve()
        if not db.is_relative_to(ROOT) or db.suffix != ".db":
            raise KBError("KB.DATABASE_OUTSIDE_PROJECT", 422)
        creds = {}
        for entry in data["credentials"]:
            key = entry["token_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", key) or key in creds:
                raise ValueError()
            roles = entry["workspaces"]
            if not roles or any(v not in ("reader", "editor") for v in roles.values()):
                raise ValueError()
            if not entry["tenant"] or not entry["subject"]:
                raise ValueError()
            audience = tuple(entry.get("audiences", ["all"]))
            if not audience or set(audience) - {"all", "internal"}:
                raise ValueError()
            creds[key] = Principal(entry["subject"], entry["tenant"], roles, audience)
        if not creds:
            raise ValueError()
        return Config(
            db,
            creds,
            data.get("embedding", {}),
            data.get("generation", {}),
            data.get("reranking", {}),
        )
    except (KeyError, ValueError, TypeError, OSError):
        raise KBError("KB.INVALID_CONFIG", 422) from None
