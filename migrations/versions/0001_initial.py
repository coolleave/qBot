"""建立 qBot 初始结构，或核对并接管旧 SQL 初始化的数据库。"""
import json
import re
from pathlib import Path

from alembic import context, op
from alembic.util import CommandError
from sqlalchemy import inspect, text

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SNAPSHOT = Path(__file__).resolve().parent.parent / "snapshots"


def normalized_type(value: str) -> str:
    value = value.upper().split(" COLLATE ")[0].split(" CHARACTER SET ")[0]
    return re.sub(r"\b(TINYINT|SMALLINT|MEDIUMINT|INT|INTEGER|BIGINT)\(\d+\)", r"\1", value)


def normalized_default(value) -> str | None:
    if value is None:
        return None
    return str(value).strip("'").lower().replace(" ", "")


def validate_legacy(connection, contract: dict) -> None:
    """已有库只在字段、默认值、索引和外键符合基线时接管。"""
    inspector = inspect(connection)
    found = set(inspector.get_table_names()) & set(contract)
    if not found:
        return
    if found != set(contract):
        raise CommandError("发现部分项目表，停止迁移；请核对旧脚本执行情况，不要直接 stamp")
    problems = []
    for name, expected in contract.items():
        columns = {c["name"]: c for c in inspector.get_columns(name)}
        if set(columns) != set(expected["columns"]):
            problems.append(f"{name}: 字段清单不同")
        for column, spec in expected["columns"].items():
            actual = columns.get(column)
            if actual is None:
                continue
            actual_type = normalized_type(str(actual["type"].compile(dialect=connection.dialect)))
            default = actual.get("default")
            if (actual_type != spec["type"] or actual["nullable"] != spec["nullable"]
                    or normalized_default(default) != normalized_default(spec["default"])):
                problems.append(f"{name}.{column}: 类型、可空性或默认值不同")
        if inspector.get_pk_constraint(name)["constrained_columns"] != expected["primary_key"]:
            problems.append(f"{name}: 主键不同")
        indexes = {i["name"]: i for i in inspector.get_indexes(name)}
        for index, spec in expected["indexes"].items():
            actual = indexes.get(index)
            if not actual or actual["column_names"] != spec["columns"] or bool(actual["unique"]) != spec["unique"]:
                problems.append(f"{name}.{index}: 索引不同")
        foreign_keys = {f["name"]: f for f in inspector.get_foreign_keys(name)}
        for key, spec in expected["foreign_keys"].items():
            actual = foreign_keys.get(key)
            if not actual or any(actual[k] != v for k, v in spec.items()):
                problems.append(f"{name}.{key}: 外键不同")
            elif any(actual.get("options", {}).get(action, "RESTRICT").upper() not in ("RESTRICT", "NO ACTION")
                     for action in ("ondelete", "onupdate")):
                problems.append(f"{name}.{key}: 外键操作不同")
        # 字符集、大小写比较与 unsigned 类型同样属于初始结构约定。
        options = inspector.get_table_options(name)
        if options.get("mysql_engine", "").lower() != "innodb" or options.get("mysql_collate") != "utf8mb4_unicode_ci":
            problems.append(f"{name}: 引擎或排序规则不同")
        details = connection.execute(text(
            "SELECT COLUMN_NAME, COLLATION_NAME, EXTRA FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
        ), {"name": name}).mappings()
        for detail in details:
            spec = expected["columns"].get(detail["COLUMN_NAME"])
            if spec and (detail["COLLATION_NAME"] != spec["collation"]
                         or ("auto_increment" in detail["EXTRA"].lower()) != spec["auto_increment"]):
                problems.append(f"{name}.{detail['COLUMN_NAME']}: 排序规则或自增属性不同")
    invalid_settings = connection.execute(text("SELECT id FROM app_settings WHERE id <> 1 LIMIT 1")).first()
    if invalid_settings:
        problems.append("app_settings: 存在非单例配置行")
    if problems:
        raise CommandError("旧库结构与初始迁移不符，未登记版本：" + "；".join(problems))


def upgrade() -> None:
    contract = json.loads((SNAPSHOT / "0001_contract.json").read_text(encoding="utf-8"))
    if not context.is_offline_mode():
        validate_legacy(op.get_bind(), contract)
    # 固化历史 SQL，不引用日后可能修改的业务代码或外部初始化脚本。
    sql = (SNAPSHOT / "0001_initial.sql").read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    # 此快照无过程/函数及字符串内分号；测试固定验证此约定。
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(text(statement.strip()))


def downgrade() -> None:
    # 初始版本拥有全部聊天历史与供应商密钥，禁止通过回退误删整库业务表。
    raise CommandError("初始迁移不支持回退到 base，以免删除聊天记录与供应商配置；请通过新迁移修正结构")
