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

    # 迁移：为旧数据库添加 new_package 列
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('tasks')]
    if 'new_package' not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN new_package BOOLEAN DEFAULT 0"))
            conn.commit()
        print("[DB Migration] 已添加 tasks.new_package 列")
