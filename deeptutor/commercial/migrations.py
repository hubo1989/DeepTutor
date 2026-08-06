"""Discovery and checksum validation for replayable PostgreSQL migrations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
import re

DEFAULT_MIGRATIONS_DIR = resources.files("deeptutor.commercial").joinpath("sql")
_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str
    path: Traversable


def discover_migrations(
    directory: Path | Traversable | None = None,
) -> tuple[Migration, ...]:
    root = directory or DEFAULT_MIGRATIONS_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"commercial migrations directory not found: {root}")
    migrations: list[Migration] = []
    seen: set[int] = set()
    paths = sorted(
        (path for path in root.iterdir() if path.name.endswith(".sql")),
        key=lambda path: path.name,
    )
    for path in paths:
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"invalid commercial migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen:
            raise ValueError(f"duplicate migration version: {version}")
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                sql=sql,
                checksum=sha256(sql.encode("utf-8")).hexdigest(),
                path=path,
            )
        )
    if not migrations:
        raise ValueError(f"no commercial migrations found in {root}")
    return tuple(migrations)
