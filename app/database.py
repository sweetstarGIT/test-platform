"""SQLite 数据库连接"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)

    # 迁移：为旧数据库补充新增列。项目当前未引入 Alembic，先使用轻量迁移。
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('tasks')]

    migrations = [
        ("new_package", "ALTER TABLE tasks ADD COLUMN new_package BOOLEAN DEFAULT 0"),
        ("external_task_id", "ALTER TABLE tasks ADD COLUMN external_task_id VARCHAR(128)"),
        ("external_status", "ALTER TABLE tasks ADD COLUMN external_status VARCHAR(50) DEFAULT ''"),
        ("artifact_url", "ALTER TABLE tasks ADD COLUMN artifact_url TEXT DEFAULT ''"),
        ("callback_url", "ALTER TABLE tasks ADD COLUMN callback_url VARCHAR(500) DEFAULT ''"),
        ("callback_sent", "ALTER TABLE tasks ADD COLUMN callback_sent BOOLEAN DEFAULT 0"),
        ("callback_error", "ALTER TABLE tasks ADD COLUMN callback_error TEXT DEFAULT ''"),
    ]

    with engine.connect() as conn:
        for column, statement in migrations:
            if column not in columns:
                conn.execute(text(statement))
                print(f"[DB Migration] 已添加 tasks.{column} 列")
        conn.commit()

    if inspector.has_table("ci_jobs"):
        ci_columns = [c['name'] for c in inspector.get_columns('ci_jobs')]
        ci_migrations = [
            ("external_task_id", "ALTER TABLE ci_jobs ADD COLUMN external_task_id VARCHAR(128) DEFAULT ''"),
            ("package_name", "ALTER TABLE ci_jobs ADD COLUMN package_name VARCHAR(255) DEFAULT ''"),
            ("artifact_url", "ALTER TABLE ci_jobs ADD COLUMN artifact_url TEXT DEFAULT ''"),
            ("artifact_filename", "ALTER TABLE ci_jobs ADD COLUMN artifact_filename VARCHAR(255) DEFAULT ''"),
            ("artifact_type", "ALTER TABLE ci_jobs ADD COLUMN artifact_type VARCHAR(20) DEFAULT ''"),
            ("task_status", "ALTER TABLE ci_jobs ADD COLUMN task_status VARCHAR(50) DEFAULT ''"),
            ("status", "ALTER TABLE ci_jobs ADD COLUMN status VARCHAR(30) DEFAULT 'received'"),
            ("current_step", "ALTER TABLE ci_jobs ADD COLUMN current_step VARCHAR(50) DEFAULT 'received'"),
            ("device_serial", "ALTER TABLE ci_jobs ADD COLUMN device_serial VARCHAR(100) DEFAULT ''"),
            ("package_id", "ALTER TABLE ci_jobs ADD COLUMN package_id INTEGER"),
            ("task_id", "ALTER TABLE ci_jobs ADD COLUMN task_id INTEGER"),
            ("report_id", "ALTER TABLE ci_jobs ADD COLUMN report_id INTEGER"),
            ("callback_url", "ALTER TABLE ci_jobs ADD COLUMN callback_url VARCHAR(500) DEFAULT ''"),
            ("callback_sent", "ALTER TABLE ci_jobs ADD COLUMN callback_sent BOOLEAN DEFAULT 0"),
            ("callback_error", "ALTER TABLE ci_jobs ADD COLUMN callback_error TEXT DEFAULT ''"),
            ("error", "ALTER TABLE ci_jobs ADD COLUMN error TEXT DEFAULT ''"),
            ("new_package", "ALTER TABLE ci_jobs ADD COLUMN new_package BOOLEAN DEFAULT 1"),
            ("events", "ALTER TABLE ci_jobs ADD COLUMN events TEXT DEFAULT '[]'"),
            ("created_at", "ALTER TABLE ci_jobs ADD COLUMN created_at DATETIME"),
            ("updated_at", "ALTER TABLE ci_jobs ADD COLUMN updated_at DATETIME"),
            ("finished_at", "ALTER TABLE ci_jobs ADD COLUMN finished_at DATETIME"),
        ]
        with engine.connect() as conn:
            for column, statement in ci_migrations:
                if column not in ci_columns:
                    conn.execute(text(statement))
                    print(f"[DB Migration] 已添加 ci_jobs.{column} 列")
            conn.commit()
