#!/usr/bin/env python3
"""Ordered PostgreSQL migration executor with an append-only checksum ledger."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_ROOT = PLATFORM_ROOT / "migrations"
COMPOSE_PATH = PLATFORM_ROOT / "deploy/docker-compose.platform.yml"
MIGRATION_PATTERN = re.compile(r"(?P<version>[0-9]{4})_[a-z0-9_]+\.sql")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DATABASE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
LEDGER_TABLE = "algorithm_schema_migrations"
LEDGER_COMMENT = "算法调度平台前向数据库迁移账本，迁移文件一旦执行不得改写"


class MigrationError(RuntimeError):
    """Raised when migration history is incomplete, changed or cannot advance."""


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    filename: str
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    checksum_sha256: str
    sql: str

    def body_sql(self) -> str:
        lines = self.sql.strip().splitlines()
        if not lines or lines[0].strip().upper() != "BEGIN;":
            raise MigrationError(f"迁移缺少外层 BEGIN: {self.filename}")
        if lines[-1].strip().upper() != "COMMIT;":
            raise MigrationError(f"迁移缺少外层 COMMIT: {self.filename}")
        body = "\n".join(lines[1:-1]).strip()
        if re.search(r"(?im)^\s*(BEGIN|COMMIT)\s*;", body):
            raise MigrationError(f"迁移包含嵌套事务边界: {self.filename}")
        return body

    def transaction_sql(self) -> str:
        body = self.body_sql()
        return (
            "BEGIN;\n"
            + body
            + "\n\nINSERT INTO algorithm_schema_migrations "
            + "(version, filename, checksum_sha256, applied_git_sha) VALUES "
            + f"({self.version}, '{self.filename}', '{self.checksum_sha256}', "
            + ":'migration_git_sha');\nCOMMIT;\n"
        )


def discover_migrations(root: Path) -> list[Migration]:
    if root.is_symlink() or not root.is_dir():
        raise MigrationError(f"迁移目录无效: {root}")
    migrations: list[Migration] = []
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise MigrationError(f"迁移文件命名或类型无效: {path.name}")
        content = path.read_text(encoding="utf-8")
        migration = Migration(
            version=int(match.group("version")),
            filename=path.name,
            checksum_sha256=hashlib.sha256(content.encode()).hexdigest(),
            sql=content,
        )
        migration.transaction_sql()
        migrations.append(migration)
    expected = list(range(1, len(migrations) + 1))
    versions = [migration.version for migration in migrations]
    if not migrations or versions != expected:
        raise MigrationError(
            f"迁移版本必须从 0001 连续递增: expected={expected}, actual={versions}"
        )
    return migrations


class MigrationDatabase(Protocol):
    def ensure_ledger(self) -> None: ...

    def read_ledger(self) -> list[AppliedMigration]: ...

    def apply(self, migration: Migration) -> None: ...

    def adopt_existing(self, migrations: Sequence[Migration]) -> int: ...


class MigrationExecutor:
    def __init__(
        self,
        database: MigrationDatabase,
        migrations: Sequence[Migration],
    ) -> None:
        self._database = database
        self._migrations = list(migrations)

    def run(self) -> list[int]:
        self._database.ensure_ledger()
        applied = self._database.read_ledger()
        self._validate_applied(applied)
        executed: list[int] = []
        for migration in self._migrations[len(applied) :]:
            self._database.apply(migration)
            executed.append(migration.version)
        return executed

    def adopt_existing(self) -> list[int]:
        self._database.ensure_ledger()
        applied = self._database.read_ledger()
        self._validate_applied(applied)
        if applied:
            return []
        adopted_count = self._database.adopt_existing(self._migrations)
        if adopted_count == 0:
            return []
        adopted = self._database.read_ledger()
        self._validate_applied(adopted)
        if len(adopted) != adopted_count:
            raise MigrationError("既有 schema 采纳后的迁移账本版本数量不正确")
        return [migration.version for migration in self._migrations[:adopted_count]]

    def _validate_applied(self, applied: Sequence[AppliedMigration]) -> None:
        by_version = {migration.version: migration for migration in self._migrations}
        if len(applied) != len({row.version for row in applied}):
            raise MigrationError("迁移账本包含重复版本")
        for row in applied:
            if row.version not in by_version:
                raise MigrationError(f"数据库包含未知迁移版本: {row.version:04d}")
        expected_applied = list(range(1, len(applied) + 1))
        if sorted(row.version for row in applied) != expected_applied:
            raise MigrationError("迁移账本版本不连续")
        for row in applied:
            migration = by_version[row.version]
            if (
                row.filename != migration.filename
                or row.checksum_sha256 != migration.checksum_sha256
            ):
                raise MigrationError(f"迁移账本与当前文件不一致: {migration.filename}")


class DockerComposePostgres:
    """Run psql inside the authoritative PostgreSQL Compose service."""

    def __init__(
        self,
        *,
        platform_root: Path,
        compose_path: Path,
        git_sha: str,
        database_name: str = "algorithm",
    ) -> None:
        if SHA_PATTERN.fullmatch(git_sha) is None:
            raise MigrationError("git_sha 必须是完整小写 Git SHA")
        if DATABASE_NAME_PATTERN.fullmatch(database_name) is None:
            raise MigrationError("PostgreSQL 数据库名称不安全")
        self._platform_root = platform_root
        self._compose_path = compose_path
        self._git_sha = git_sha
        self._database_name = database_name
        self._ledger_signature_md5: str | None = None

    def _psql(self, sql: str, *, tuples_only: bool = False) -> str:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self._platform_root / "deploy"),
            "-f",
            str(self._compose_path),
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            "algorithm",
            "--dbname",
            self._database_name,
            "--no-psqlrc",
            "--quiet",
            "--set=ON_ERROR_STOP=1",
        ]
        if tuples_only:
            command.extend(("--tuples-only", "--no-align", "--field-separator=\t"))
        completed = subprocess.run(
            command,
            input="SET search_path TO public;\n" + sql,
            text=True,
            capture_output=True,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            stderr_digest = hashlib.sha256(completed.stderr.encode()).hexdigest()[:16]
            raise MigrationError(
                "PostgreSQL 迁移命令失败: "
                f"exit_code={completed.returncode}, stderr_sha256={stderr_digest}"
            )
        return completed.stdout

    def _schema_signature_ctes(
        self,
        schema_name: str,
        *,
        only_table: str | None = None,
    ) -> str:
        """Build the catalog signature shared by prefix matching and locked rechecks."""

        if DATABASE_NAME_PATTERN.fullmatch(schema_name) is None:
            raise MigrationError("PostgreSQL schema 名称不安全")
        if only_table is not None and DATABASE_NAME_PATTERN.fullmatch(only_table) is None:
            raise MigrationError("PostgreSQL 表名称不安全")
        table_filter = (
            f"AND cls.relname = '{only_table}'"
            if only_table is not None
            else f"AND cls.relname <> '{LEDGER_TABLE}'"
        )
        sequence_filter = "AND false" if only_table is not None else ""
        return f"""target_tables(name) AS (
    SELECT cls.relname
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = '{schema_name}'
      AND cls.relkind IN ('r', 'p')
      {table_filter}
),
signature AS (
    SELECT 'table' AS kind, cls.relname AS object_name,
           concat_ws('|', cls.relkind::text, cls.relpersistence::text,
                     cls.relrowsecurity::text, cls.relforcerowsecurity::text,
                     cls.relreplident::text,
                     coalesce(am.amname, '<NONE>'),
                     pg_get_userbyid(cls.relowner),
                     coalesce(cls.relacl::text, '<NULL>'),
                     coalesce(cls.reloptions::text, '<NULL>'),
                     coalesce(tblspc.spcname, '<DEFAULT>')) AS definition
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    LEFT JOIN pg_am am ON am.oid = cls.relam
    LEFT JOIN pg_tablespace tblspc ON tblspc.oid = cls.reltablespace
    WHERE ns.nspname = '{schema_name}' AND cls.relkind IN ('r', 'p')
    UNION ALL
    SELECT 'column', cls.relname || '.' || attr.attname,
           concat_ws('|', pg_catalog.format_type(attr.atttypid, attr.atttypmod),
                     attr.attnotnull::text, attr.attidentity::text,
                     attr.attgenerated::text, attr.attstorage::text,
                     attr.attcompression::text,
                     coalesce(coll_ns.nspname || '.' || coll.collname, '<NONE>'),
                     coalesce(replace(replace(pg_get_expr(def.adbin, def.adrelid),
                         quote_ident('{schema_name}') || '.', '<schema>.'),
                         '{schema_name}.', '<schema>.'), '<NULL>'),
                     coalesce(attr.attacl::text, '<NULL>'),
                     coalesce(attr.attoptions::text, '<NULL>'),
                     coalesce(attr.attfdwoptions::text, '<NULL>'))
    FROM pg_attribute attr
    JOIN pg_class cls ON cls.oid = attr.attrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    LEFT JOIN pg_attrdef def ON def.adrelid = attr.attrelid AND def.adnum = attr.attnum
    LEFT JOIN pg_collation coll ON coll.oid = attr.attcollation
    LEFT JOIN pg_namespace coll_ns ON coll_ns.oid = coll.collnamespace
    WHERE ns.nspname = '{schema_name}' AND attr.attnum > 0 AND NOT attr.attisdropped
    UNION ALL
    SELECT 'constraint', cls.relname || '.' || con.conname,
           concat_ws('|', replace(replace(pg_get_constraintdef(con.oid, true),
                         quote_ident('{schema_name}') || '.', '<schema>.'),
                         '{schema_name}.', '<schema>.'),
                     con.condeferrable::text, con.condeferred::text,
                     con.convalidated::text, con.connoinherit::text)
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}'
    UNION ALL
    SELECT 'index', tab.relname || '.' || idx.relname,
           concat_ws('|', replace(replace(pg_get_indexdef(idx.oid),
                         quote_ident('{schema_name}') || '.', '<schema>.'),
                         '{schema_name}.', '<schema>.'),
                     ind.indisunique::text,
                     ind.indisprimary::text, ind.indisvalid::text,
                     ind.indisready::text, ind.indislive::text,
                     ind.indisclustered::text, ind.indisreplident::text,
                     ind.indnullsnotdistinct::text)
    FROM pg_index ind
    JOIN pg_class idx ON idx.oid = ind.indexrelid
    JOIN pg_class tab ON tab.oid = ind.indrelid
    JOIN pg_namespace ns ON ns.oid = tab.relnamespace
    JOIN target_tables target ON target.name = tab.relname
    WHERE ns.nspname = '{schema_name}'
    UNION ALL
    SELECT 'trigger', cls.relname || '.' || trg.tgname,
           concat_ws('|', replace(replace(pg_get_triggerdef(trg.oid, true),
                         quote_ident('{schema_name}') || '.', '<schema>.'),
                         '{schema_name}.', '<schema>.'),
                     trg.tgenabled::text)
    FROM pg_trigger trg
    JOIN pg_class cls ON cls.oid = trg.tgrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}' AND NOT trg.tgisinternal
    UNION ALL
    SELECT 'policy', cls.relname || '.' || pol.polname,
           concat_ws('|', pol.polcmd::text, pol.polpermissive::text,
                     pol.polroles::text,
                     coalesce(pg_get_expr(pol.polqual, pol.polrelid), '<NULL>'),
                     coalesce(pg_get_expr(pol.polwithcheck, pol.polrelid), '<NULL>'))
    FROM pg_policy pol
    JOIN pg_class cls ON cls.oid = pol.polrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}'
    UNION ALL
    SELECT 'rule', cls.relname || '.' || rewrite.rulename,
           replace(replace(pg_get_ruledef(rewrite.oid, true),
                   quote_ident('{schema_name}') || '.', '<schema>.'),
                   '{schema_name}.', '<schema>.')
    FROM pg_rewrite rewrite
    JOIN pg_class cls ON cls.oid = rewrite.ev_class
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}' AND rewrite.rulename <> '_RETURN'
    UNION ALL
    SELECT 'table_comment', cls.relname, coalesce(obj_description(cls.oid, 'pg_class'), '<NULL>')
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}' AND cls.relkind = 'r'
    UNION ALL
    SELECT 'column_comment', cls.relname || '.' || attr.attname,
           coalesce(col_description(cls.oid, attr.attnum), '<NULL>')
    FROM pg_attribute attr
    JOIN pg_class cls ON cls.oid = attr.attrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN target_tables target ON target.name = cls.relname
    WHERE ns.nspname = '{schema_name}' AND attr.attnum > 0 AND NOT attr.attisdropped
    UNION ALL
    SELECT 'sequence', seq.relname,
           concat_ws('|', seq.relpersistence::text,
                     pg_catalog.format_type(pgseq.seqtypid, NULL),
                     pgseq.seqstart::text, pgseq.seqincrement::text,
                     pgseq.seqmin::text, pgseq.seqmax::text, pgseq.seqcache::text,
                     pgseq.seqcycle::text,
                     coalesce(dep.deptype::text, '<NULL>'),
                     coalesce((CASE WHEN owner_ns.nspname = '{schema_name}'
                                    THEN '<schema>' ELSE owner_ns.nspname END) || '.' ||
                              owner.relname || '.' || owner_attr.attname, '<NULL>'),
                     pg_get_userbyid(seq.relowner),
                     coalesce(seq.relacl::text, '<NULL>'),
                     coalesce(obj_description(seq.oid, 'pg_class'), '<NULL>'))
    FROM pg_class seq
    JOIN pg_namespace ns ON ns.oid = seq.relnamespace
    JOIN pg_sequence pgseq ON pgseq.seqrelid = seq.oid
    LEFT JOIN pg_depend dep
      ON dep.classid = 'pg_class'::regclass
     AND dep.objid = seq.oid
     AND dep.refclassid = 'pg_class'::regclass
     AND dep.deptype IN ('a', 'i')
    LEFT JOIN pg_class owner ON owner.oid = dep.refobjid
    LEFT JOIN pg_namespace owner_ns ON owner_ns.oid = owner.relnamespace
    LEFT JOIN pg_attribute owner_attr
      ON owner_attr.attrelid = owner.oid AND owner_attr.attnum = dep.refobjsubid
    WHERE ns.nspname = '{schema_name}' {sequence_filter}
)
"""

    def _schema_signature_digest(
        self,
        schema_name: str,
        *,
        only_table: str | None = None,
    ) -> str:
        ctes = self._schema_signature_ctes(schema_name, only_table=only_table)
        output = self._psql(
            f"""
SET search_path TO "{schema_name}";
WITH {ctes}
SELECT md5(coalesce(string_agg(
    concat_ws(E'\\t', kind, object_name, definition), E'\\n'
    ORDER BY kind, object_name, definition
), ''))
FROM signature;
""",
            tuples_only=True,
        )
        digests = [
            line.strip()
            for line in output.splitlines()
            if re.fullmatch(r"[0-9a-f]{32}", line.strip())
        ]
        if len(digests) != 1:
            raise MigrationError("PostgreSQL schema 签名摘要无效")
        return digests[0]

    def _schema_table_names(self, schema_name: str) -> list[str]:
        if DATABASE_NAME_PATTERN.fullmatch(schema_name) is None:
            raise MigrationError("PostgreSQL schema 名称不安全")
        output = self._psql(
            f"""
SELECT cls.relname
FROM pg_class cls
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
WHERE ns.nspname = '{schema_name}'
  AND cls.relkind IN ('r', 'p')
  AND cls.relname <> 'algorithm_schema_migrations'
ORDER BY cls.relname;
""",
            tuples_only=True,
        )
        names = [line.strip() for line in output.splitlines() if line.strip()]
        if any(DATABASE_NAME_PATTERN.fullmatch(name) is None for name in names):
            raise MigrationError("PostgreSQL schema 表清单不安全")
        return names

    def _schema_sequence_names(self, schema_name: str) -> list[str]:
        if DATABASE_NAME_PATTERN.fullmatch(schema_name) is None:
            raise MigrationError("PostgreSQL schema 名称不安全")
        output = self._psql(
            f"""
SELECT cls.relname
FROM pg_class cls
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
JOIN pg_sequence seq ON seq.seqrelid = cls.oid
WHERE ns.nspname = '{schema_name}'
ORDER BY cls.relname;
""",
            tuples_only=True,
        )
        sequences: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            name = line.strip()
            if DATABASE_NAME_PATTERN.fullmatch(name) is None:
                raise MigrationError("PostgreSQL schema 序列清单不安全")
            sequences.append(name)
        return sequences

    def _schema_owned_sequences(
        self,
        schema_name: str,
    ) -> list[tuple[str, str, str, int]]:
        if DATABASE_NAME_PATTERN.fullmatch(schema_name) is None:
            raise MigrationError("PostgreSQL schema 名称不安全")
        output = self._psql(
            f"""
SELECT seq.relname, owner.relname, attr.attname, pgseq.seqincrement
FROM pg_class seq
JOIN pg_namespace seq_ns ON seq_ns.oid = seq.relnamespace
JOIN pg_sequence pgseq ON pgseq.seqrelid = seq.oid
JOIN pg_depend dep
  ON dep.classid = 'pg_class'::regclass
 AND dep.objid = seq.oid
 AND dep.refclassid = 'pg_class'::regclass
 AND dep.deptype IN ('a', 'i')
JOIN pg_class owner ON owner.oid = dep.refobjid
JOIN pg_namespace owner_ns ON owner_ns.oid = owner.relnamespace
JOIN pg_attribute attr
  ON attr.attrelid = owner.oid AND attr.attnum = dep.refobjsubid
WHERE seq_ns.nspname = '{schema_name}'
  AND owner_ns.nspname = '{schema_name}'
ORDER BY seq.relname;
""",
            tuples_only=True,
        )
        sequences: list[tuple[str, str, str, int]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if (
                len(parts) != 4
                or any(DATABASE_NAME_PATTERN.fullmatch(name) is None for name in parts[:3])
            ):
                raise MigrationError("PostgreSQL 自增序列映射不安全")
            try:
                increment = int(parts[3])
            except ValueError as error:
                raise MigrationError("PostgreSQL 自增序列步长无效") from error
            if increment == 0:
                raise MigrationError("PostgreSQL 自增序列步长无效")
            sequences.append((parts[0], parts[1], parts[2], increment))
        return sequences

    def _ledger_ddl(self, schema_name: str) -> str:
        if DATABASE_NAME_PATTERN.fullmatch(schema_name) is None:
            raise MigrationError("PostgreSQL schema 名称不安全")
        qualified_table = f'"{schema_name}"."{LEDGER_TABLE}"'
        return f"""
CREATE TABLE {qualified_table} (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL UNIQUE,
    checksum_sha256 char(64) NOT NULL,
    applied_git_sha char(40) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CHECK (checksum_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (applied_git_sha ~ '^[0-9a-f]{{40}}$')
);
COMMENT ON TABLE {qualified_table} IS '{LEDGER_COMMENT}';
"""

    def _public_ledger_exists(self) -> bool:
        output = self._psql(
            "SELECT CASE WHEN to_regclass('public.algorithm_schema_migrations') "
            "IS NULL THEN '0' ELSE '1' END;\n",
            tuples_only=True,
        )
        values = [line.strip() for line in output.splitlines() if line.strip()]
        if values not in (["0"], ["1"]):
            raise MigrationError("PostgreSQL 迁移账本存在性查询结果无效")
        return values == ["1"]

    def _assert_ledger_structure(self) -> str:
        schema_name = f"algorithm_ledger_{uuid4().hex}"
        self._psql(
            f'CREATE SCHEMA "{schema_name}";\n'
            + self._ledger_ddl(schema_name)
        )
        try:
            expected_digest = self._schema_signature_digest(
                schema_name,
                only_table=LEDGER_TABLE,
            )
            actual_digest = self._schema_signature_digest(
                "public",
                only_table=LEDGER_TABLE,
            )
        finally:
            self._psql(f'DROP SCHEMA "{schema_name}" CASCADE;\n')
        if actual_digest != expected_digest:
            raise MigrationError("public 迁移账本结构与平台合同不一致")
        return expected_digest

    def _assert_no_non_public_ledger(self) -> None:
        output = self._psql(
            """
SELECT ns.nspname
FROM pg_class cls
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
WHERE cls.relname = 'algorithm_schema_migrations'
  AND cls.relkind IN ('r', 'p')
  AND ns.nspname <> 'public'
  AND ns.nspname NOT LIKE 'pg_%'
  AND ns.nspname <> 'information_schema';
""",
            tuples_only=True,
        )
        if any(line.strip() for line in output.splitlines()):
            raise MigrationError("检测到非 public 迁移账本，拒绝继续")

    def adopt_existing(self, migrations: Sequence[Migration]) -> int:
        if self._ledger_signature_md5 is None:
            raise MigrationError("迁移账本尚未通过结构校验")
        table_names = self._schema_table_names("public")
        sequence_names = self._schema_sequence_names("public")
        owned_sequences = self._schema_owned_sequences("public")
        if not table_names and not sequence_names:
            return 0
        public_signature_md5 = self._schema_signature_digest("public")

        schema_name = f"algorithm_adopt_{uuid4().hex}"
        matching_prefixes: list[tuple[int, str]] = []
        self._psql(f'CREATE SCHEMA "{schema_name}";\n')
        try:
            for migration in migrations:
                self._psql(
                    "BEGIN;\n"
                    f'SET LOCAL search_path TO "{schema_name}", public;\n'
                    + migration.body_sql()
                    + "\nCOMMIT;\n"
                )
                prefix_signature_md5 = self._schema_signature_digest(schema_name)
                if prefix_signature_md5 == public_signature_md5:
                    matching_prefixes.append((migration.version, prefix_signature_md5))
        finally:
            self._psql(f'DROP SCHEMA "{schema_name}" CASCADE;\n')

        if len(matching_prefixes) != 1:
            raise MigrationError(
                "既有 PostgreSQL schema 与连续迁移前缀不一致: "
                f"matching_prefixes={[version for version, _digest in matching_prefixes]}"
            )
        adopted_count, expected_signature_md5 = matching_prefixes[0]

        adopted_migrations = migrations[:adopted_count]
        values = ",\n".join(
            "(" + ", ".join(
                (
                    str(migration.version),
                    f"'{migration.filename}'",
                    f"'{migration.checksum_sha256}'",
                    f"'{self._git_sha}'",
                )
            ) + ")"
            for migration in adopted_migrations
        )
        lock_statements = "\n".join(
            f'LOCK TABLE public."{name}" IN ACCESS EXCLUSIVE MODE;' for name in table_names
        )
        sequence_lock_statements = """
DO $sequence_relation_lock$
DECLARE target record;
BEGIN
  -- 在目录锁和空事务门禁后读当前值，避免用预扫描旧值覆盖并发漂移。
  FOR target IN
    SELECT format('%I.%I', ns.nspname, cls.relname) AS qualified_name,
           seq.seqcache
    FROM pg_catalog.pg_sequence seq
    JOIN pg_catalog.pg_class cls ON cls.oid = seq.seqrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = 'public'
    ORDER BY cls.relname
  LOOP
    EXECUTE format(
        'ALTER SEQUENCE %s CACHE %s',
        target.qualified_name,
        target.seqcache
    );
  END LOOP;
END
$sequence_relation_lock$;
"""
        sequence_state_checks: list[str] = []
        for sequence_name, table_name, column_name, increment in owned_sequences:
            sequence_regclass = f"'public.\"{sequence_name}\"'::regclass"
            next_value = f"""(SELECT CASE WHEN is_called
                      THEN last_value::numeric + seq.seqincrement::numeric
                      ELSE last_value::numeric END
          FROM public."{sequence_name}"
          CROSS JOIN pg_catalog.pg_sequence seq
          WHERE seq.seqrelid = {sequence_regclass})"""
            sequence_state_checks.append(
                f"""
  IF EXISTS (SELECT 1 FROM public."{table_name}")
     AND {next_value}
         {"<=" if increment > 0 else ">="}
         (SELECT {"max" if increment > 0 else "min"}("{column_name}")::numeric
          FROM public."{table_name}") THEN
    RAISE EXCEPTION '既有 schema 自增序列无法生成未使用键';
  END IF;
  IF (SELECT seqcycle FROM pg_catalog.pg_sequence
      WHERE seqrelid = {sequence_regclass})
     OR {next_value} NOT BETWEEN
        (SELECT seqmin::numeric FROM pg_catalog.pg_sequence
         WHERE seqrelid = {sequence_regclass})
        AND
        (SELECT seqmax::numeric FROM pg_catalog.pg_sequence
         WHERE seqrelid = {sequence_regclass}) THEN
    RAISE EXCEPTION '既有 schema 自增序列已耗尽或使用循环配置';
  END IF;
"""
            )
        sequence_state_validation = "".join(sequence_state_checks)
        data_validation = sequence_state_validation
        if adopted_count >= 6:
            data_validation += """
  IF EXISTS (
      SELECT 1 FROM public.course_task_types
      WHERE submission_id = '00000000-0000-0000-0000-000000000000'::uuid
  ) THEN
    RAISE EXCEPTION '既有 schema 包含无效 submission_id 回填值';
  END IF;
  IF EXISTS (
      SELECT 1
      FROM public.course_task_types
      GROUP BY submission_id
      HAVING count(DISTINCT task_id) > 1
  ) THEN
    RAISE EXCEPTION '既有 schema 包含跨课程复用的 submission_id';
  END IF;
"""
        signature_ctes = self._schema_signature_ctes("public")
        ledger_signature_ctes = self._schema_signature_ctes(
            "public",
            only_table=LEDGER_TABLE,
        )
        signature_check = f"""
DO $adopt$
DECLARE actual_signature_md5 text;
DECLARE actual_ledger_signature_md5 text;
BEGIN
  IF EXISTS (SELECT 1 FROM public.algorithm_schema_migrations) THEN
    RAISE EXCEPTION 'migration ledger is no longer empty';
  END IF;
  WITH {ledger_signature_ctes}
  SELECT md5(coalesce(string_agg(
      concat_ws(E'\\t', kind, object_name, definition), E'\\n'
      ORDER BY kind, object_name, definition
  ), ''))
  INTO actual_ledger_signature_md5
  FROM signature;
  IF actual_ledger_signature_md5 IS DISTINCT FROM '{self._ledger_signature_md5}' THEN
    RAISE EXCEPTION 'migration ledger structure changed before adoption';
  END IF;
  WITH {signature_ctes}
  SELECT md5(coalesce(string_agg(
      concat_ws(E'\\t', kind, object_name, definition), E'\\n'
      ORDER BY kind, object_name, definition
  ), ''))
  INTO actual_signature_md5
  FROM signature;
  IF actual_signature_md5 IS DISTINCT FROM '{expected_signature_md5}' THEN
    RAISE EXCEPTION '既有 PostgreSQL schema 在采纳前发生变化';
  END IF;
{data_validation}END
$adopt$;
"""
        maintenance_guard = """
DO $maintenance_guard$
BEGIN
  IF EXISTS (
      SELECT 1
      FROM pg_catalog.pg_stat_activity
      WHERE datname = current_database()
        AND pid <> pg_backend_pid()
        AND backend_type = 'client backend'
        AND xact_start IS NOT NULL
  ) OR EXISTS (
      SELECT 1
      FROM pg_catalog.pg_prepared_xacts
      WHERE database = current_database()
  ) THEN
    RAISE EXCEPTION '旧库采纳要求维护窗口内无其他事务或预处理事务';
  END IF;
END
$maintenance_guard$;
"""
        self._psql(
            f"""BEGIN ISOLATION LEVEL READ COMMITTED;
SET LOCAL lock_timeout TO '30s';
LOCK TABLE pg_catalog.pg_class IN SHARE MODE;
{maintenance_guard}
LOCK TABLE public.algorithm_schema_migrations IN ACCESS EXCLUSIVE MODE;
{lock_statements}
{sequence_lock_statements}
{signature_check}
INSERT INTO public.algorithm_schema_migrations
(version, filename, checksum_sha256, applied_git_sha) VALUES
{values};
COMMIT;
"""
        )
        return adopted_count

    def ensure_ledger(self) -> None:
        self._assert_no_non_public_ledger()
        if not self._public_ledger_exists():
            self._psql(self._ledger_ddl("public"))
        self._ledger_signature_md5 = self._assert_ledger_structure()

    def read_ledger(self) -> list[AppliedMigration]:
        output = self._psql(
            "SELECT version, filename, checksum_sha256 "
            "FROM algorithm_schema_migrations ORDER BY version;\n",
            tuples_only=True,
        )
        rows: list[AppliedMigration] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not parts[0].isdigit():
                raise MigrationError("迁移账本查询结果无效")
            rows.append(AppliedMigration(int(parts[0]), parts[1], parts[2]))
        return rows

    def apply(self, migration: Migration) -> None:
        sql = "\\set migration_git_sha '" + self._git_sha + "'\n" + migration.transaction_sql()
        self._psql(sql)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按顺序执行算法调度平台 PostgreSQL 迁移",
        allow_abbrev=False,
    )
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--platform-root", type=Path, default=PLATFORM_ROOT)
    parser.add_argument("--migrations-root", type=Path, default=MIGRATIONS_ROOT)
    parser.add_argument("--compose-path", type=Path, default=COMPOSE_PATH)
    parser.add_argument("--database-name", default="algorithm")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--adopt-existing", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    migrations = discover_migrations(args.migrations_root)
    if args.plan:
        for migration in migrations:
            print(f"{migration.version:04d} {migration.checksum_sha256} {migration.filename}")
        return 0
    database = DockerComposePostgres(
        platform_root=args.platform_root,
        compose_path=args.compose_path,
        git_sha=args.git_sha,
        database_name=args.database_name,
    )
    executor = MigrationExecutor(database, migrations)
    adopted = executor.adopt_existing() if args.adopt_existing else []
    if adopted:
        print("database-migrations: adopted " + ",".join(f"{item:04d}" for item in adopted))
    executed = executor.run()
    if executed:
        print("database-migrations: applied " + ",".join(f"{item:04d}" for item in executed))
    else:
        print("database-migrations: already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
