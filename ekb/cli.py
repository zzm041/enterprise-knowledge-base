"""Explicit synthetic bootstrap. Never reads upstream configuration."""

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from ekb.catalog import Catalog
from ekb.config import ROOT
from ekb.errors import KBError
from ekb.evaluate import populate


def initialize(directory=None):
    directory = Path(directory or ROOT / "runtime").resolve()
    if not directory.is_relative_to(ROOT):
        raise KBError("KB.CONFIG_OUTSIDE_PROJECT", 422)
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    db = directory / "knowledge.db"
    if config_path.exists() or db.exists():
        raise KBError("KB.ALREADY_INITIALIZED", 409)
    credentials = []
    tokens = {}
    for subject, tenant, role, audiences in [
        ("editor", "acme", "editor", ["all", "internal"]),
        ("reader", "acme", "reader", ["all"]),
        ("staff", "acme", "reader", ["all", "internal"]),
        ("other-reader", "other", "reader", ["all"]),
    ]:
        token = secrets.token_urlsafe(32)
        tokens[subject] = token
        credentials.append(
            {
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "subject": subject,
                "tenant": tenant,
                "workspaces": {"handbook": role},
                "audiences": audiences,
            }
        )
    config = {"database": db.relative_to(ROOT).as_posix(), "credentials": credentials}
    catalog = Catalog(db)
    corpus = json.loads(
        (ROOT / "evaluation" / "corpus.json").read_text(encoding="utf8")
    )
    populate(catalog, corpus)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf8")
    (directory / "demo-credentials.json").write_text(
        json.dumps(tokens, indent=2), encoding="utf8"
    )
    return config_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init"])
    args = parser.parse_args()
    path = initialize()
    print(
        f"Synthetic catalog created. Config: {path}. Credentials remain in runtime/demo-credentials.json."
    )
