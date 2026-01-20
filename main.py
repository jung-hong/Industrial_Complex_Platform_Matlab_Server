# main.py
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import asyncio
import os
import uuid
import pandas as pd
import shutil
from typing import Optional

# 우리가 만든 모듈
from database import get_db
from utils import process_building_geometry

# MATLAB 관련 (설치된 패키지 이름 확인 필요)
import matlab
import SNUs_dsm2irrPkg

app = FastAPI(title="Solar Simulation API")

# --- 전역 변수: MATLAB 엔진 ---
matlab_pkg = None
engine_lock = asyncio.Lock()

@app.on_event("startup")
def startup_event():
    global matlab_pkg
    print(">>> Initializing MATLAB Runtime... (Please wait)")
    try:
        matlab_pkg = SNUs_dsm2irrPkg.initialize()
        print(">>> MATLAB Initialized Successfully.")
    except Exception as e:
        print(f">>> MATLAB Initialization Failed: {e}")

@app.on_event("shutdown")
def shutdown_event():
    global matlab_pkg
    if matlab_pkg:
        matlab_pkg.terminate()
        print(">>> MATLAB Terminated.")

# --- API 요청 모델 ---
# DB ID만 받으면 됨

@app.post("/simulate/{building_id}")
async def run_simulation(building_id: int, db: Session = Depends(get_db)):
    global matlab_pkg
    if not matlab_pkg:
        raise HTTPException(status_code=500, detail="MATLAB engine not ready")

    # 1. DB에서 건물 정보 조회 (Raw SQL 사용)
    # geoalchemy2가 있으면 ORM도 되지만, shapely 변환을 위해 WKB로 가져옵니다.
    query = text("""
        SELECT id, geom, gro_flo_co 
        FROM building_gis 
        WHERE id = :bid
    """)
    result = db.execute(query, {"bid": building_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Building not found")
    
    b_id, geom_wkb, floor_count = result
    
    # 2. 전처리: NPY 파일 생성
    req_uuid = str(uuid.uuid4())
    temp_dir = os.path.join("temp", req_uuid) # 임시 폴더
    
    try:
        dsm_path, roof_mask_path, facade_mask_path = process_building_geometry(
            geom_wkb, floor_count, temp_dir, f"sample_{req_uuid}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preprocessing failed: {e}")

    # 3. MATLAB 시뮬레이션 실행
    # 날씨 파일은 RE100 폴더에 있다고 가정 (경로 확인 필수)
    weather_csv = os.path.abspath("RE100/38.csv") 
    output_csv_roof = os.path.join(temp_dir, "result_roof.csv")
    
    # 결과 담을 변수
    simulation_result = {}

    async with engine_lock: # 동시 실행 방지
        try:
            # 변수 캐스팅 (MATLAB 타입)
            tmydir = weather_csv
            tmylat = matlab.double(37.6)
            tmylon = matlab.double(127.2)
            tmyele = matlab.double(129.0)
            
            # 입력 파일 경로는 절대 경로로 주는 것이 안전
            dsmdir = os.path.abspath(dsm_path)
            regionmapdir = os.path.abspath(roof_mask_path)
            
            # A. 지붕 계산 (Roof)
            print(">>> Running Roof Simulation...")
            matlab_pkg.SNUsolar_dsm2irr(
                tmydir, tmylat, tmylon, tmyele, 
                dsmdir, regionmapdir, 
                matlab.logical(False), # isfacade=False
                matlab.double(0),      # pzen=0
                matlab.double(180),    # pazi=180
                output_csv_roof
            )
            
            # 결과 파일 읽기
            if os.path.exists(output_csv_roof):
                df = pd.read_csv(output_csv_roof)
                # NaN 값을 None으로 변환 (JSON 호환성)
                simulation_result['roof'] = df.where(pd.notnull(df), None).to_dict(orient='records')
            
            # B. 필요하다면 외벽 계산(Facade)도 여기에 추가 가능
            # (regionmapdir를 facade_mask_path로 변경 후 호출)

        except Exception as e:
            print(f"MATLAB Error: {e}")
            raise HTTPException(status_code=500, detail=f"Simulation error: {str(e)}")
        finally:
            # 임시 파일 삭제 (디버깅 때는 주석 처리하세요)
            # shutil.rmtree(temp_dir, ignore_errors=True)
            pass

    return {
        "building_id": b_id,
        "floor_count": floor_count,
        "status": "success",
        "data": simulation_result
    }

if __name__ == "__main__":
    import uvicorn
    # 프로젝트 폴더 내에 RE100 폴더와 날씨 파일이 있어야 함
    uvicorn.run(app, host="0.0.0.0", port=8000)