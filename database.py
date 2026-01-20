# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 사용자 제공 정보 기반
DB_USER = "postgres"
DB_PASSWORD = "skdlsdhkxm"
DB_HOST = "34.22.74.121"
DB_PORT = "5432"
DB_NAME = "industrial_complex_db"

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()