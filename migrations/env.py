"""仅显式执行 Alembic 命令时迁移，应用启动不自动修改数据库。"""
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from qbot.config import Settings
from qbot.storage import mysql_engine

config = context.config
root = Path(config.config_file_name).resolve().parent


def run() -> None:
    # 项目当前反射已有表，没有声明式目标结构，不能据此生成结构差异。
    options = getattr(config, "cmd_opts", None)
    if getattr(options, "autogenerate", False):
        raise CommandError("当前采用手写迁移，请使用 alembic revision -m 中文说明，不使用 --autogenerate")
    if context.is_offline_mode():
        context.configure(dialect_name="mysql", literal_binds=True, dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
        return

    settings = Settings(_env_file=root / ".env")
    if not settings.mysql_database or not settings.mysql_user:
        raise CommandError("请在 .env 配置 MYSQL_DATABASE、MYSQL_USER 和数据库连接信息")
    engine = mysql_engine(settings)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "mysql" or connection.dialect.is_mariadb:
                raise CommandError("当前迁移仅支持 MySQL 5.6.5+，不支持 MariaDB")
            if connection.dialect.server_version_info < (5, 6, 5):
                raise CommandError("当前迁移要求 MySQL 5.6.5+，以支持 DATETIME 默认值")
            context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    except SQLAlchemyError:
        # 不回显驱动异常、连接地址或 SQL 参数，避免泄露凭证。
        raise CommandError("数据库迁移失败，请检查连接、迁移账号权限及表结构；不要使用 stamp 跳过失败") from None
    finally:
        engine.dispose()


run()
