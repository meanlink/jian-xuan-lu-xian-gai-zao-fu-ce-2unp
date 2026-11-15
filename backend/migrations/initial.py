"""建表与轻量升级。新增列以幂等方式补齐，已有演示卷可原地升级。"""
from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models

# 旧库升级：表名 -> [(列名, DDL 类型与默认值)]
COLUMNS = {
    'datasets': [('kind', "VARCHAR(20) NOT NULL DEFAULT 'business'")],
    'jobs': [('mode', "VARCHAR(20) NOT NULL DEFAULT 'normal'"),
             ('trial_id', 'INTEGER NULL'),
             ('trial_round', 'INTEGER NOT NULL DEFAULT 0'),
             ('started_at', 'DOUBLE NULL')],
}


def _add_missing_columns():
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in COLUMNS.items():
            if table not in existing:
                continue
            have = {c['name'] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))


def migrate():
    # New tables are created first so added columns can reference them.
    Base.metadata.create_all(engine)
    _add_missing_columns()


if __name__ == '__main__':
    migrate()
