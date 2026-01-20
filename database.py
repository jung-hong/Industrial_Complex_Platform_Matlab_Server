from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# DB 정보 (보안을 위해 환경변수로 빼는 것이 좋으나, 현재는 하드코딩 유지)
DB_USER = "postgres"
DB_PASSWORD = "skdlsdhkxm"
DB_HOST = "34.22.74.121"
DB_PORT = "5432"
DB_NAME = "industrial_complex_db"

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# pool_pre_ping=True: 연결 끊김 방지 옵션
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()