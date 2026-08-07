import re
import sys
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(?P<order>\d{4})_[a-z0-9_]+\.sql$")


def validate_migration_names(migrations_dir: Path) -> None:
    files = sorted(migrations_dir.glob("*.sql"))
    orders: list[int] = []
    for migration in files:
        match = _MIGRATION_NAME.fullmatch(migration.name)
        if match is None:
            raise ValueError(f"迁移文件名不合法: {migration.name}")
        orders.append(int(match.group("order")))

    if len(orders) != len(set(orders)):
        raise ValueError("迁移序号不能重复")
    if orders and orders != list(range(1, len(orders) + 1)):
        raise ValueError("迁移序号必须从 0001 开始连续递增")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        validate_migration_names(root / "migrations")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Migration file names are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
