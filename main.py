from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import asyncio
import os
import uuid
import pandas as pd
import shutil
import matlab
import SNUs_dsm2irrPkg  # 패키지 설치 필수

from database import get_db
from utils import create_simulation_inputs

app = FastAPI()

# MATLAB 엔진 관리
matlab_pkg = None
engine_lock = asyncio.Lock()

@app.on_event("startup")
def startup_event():
    global matlab_pkg
    print(">>> [System] Initializing MATLAB Runtime... (This takes 10-20 sec)")
    try:
        matlab_pkg = SNUs_dsm2irrPkg.initialize()
        print(">>> [System] MATLAB Initialized Successfully.")
    except Exception as e:
        print(f">>> [Error] MATLAB Init Failed: {e}")
        # MATLAB 실패 시 서버를 강제로 종료할지, 경고만 할지 결정
        # 여기선 경고만 함

@app.on_event("shutdown")
def shutdown_event():
    global matlab_pkg
    if matlab_pkg:
        matlab_pkg.terminate()
        print(">>> [System] MATLAB Terminated.")

@app.post("/simulate/{building_id}")
async def run_simulation(building_id: int, db: Session = Depends(get_db)):
    global matlab_pkg
    
    if not matlab_pkg:
        raise HTTPException(status_code=500, detail="MATLAB Engine is not active.")

    # 1. DB 조회 (타겟 + 반경 700m 주변 건물)
    try:
        # 타겟 확인
        target_res = db.execute(text("SELECT geom FROM building_gis WHERE id = :bid"), {"bid": building_id}).fetchone()
        if not target_res:
            raise HTTPException(status_code=404, detail="Building ID not found in DB")
        target_geom_raw = target_res[0]

        # 주변 검색
        query = text("""
            SELECT id, geom, COALESCE(gro_flo_co, 1) as floors 
            FROM building_gis 
            WHERE ST_DWithin(geom::geography, (SELECT geom::geography FROM building_gis WHERE id = :bid), 700)
        """)
        neighbors = db.execute(query, {"bid": building_id}).fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

    # 데이터 정리
    building_list = []
    for row in neighbors:
        bid, bgeom, floors = row
        h = float(floors) * 3.3
        if h <= 0: h = 3.3
        building_list.append({"geom": bgeom, "height": h, "is_target": (bid == building_id)})

    # 2. 전처리 (NPY 생성)
    req_uuid = str(uuid.uuid4())
    temp_dir = os.path.abspath(os.path.join("temp", req_uuid)) # 절대 경로 사용
    
    try:
        dsm_path, roof_mask, _ = create_simulation_inputs(target_geom_raw, building_list, temp_dir, "sim_input")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Geometry Processing Error: {str(e)}")

    # 3. MATLAB 시뮬레이션
    weather_csv = os.path.abspath("RE100/38.csv") # 절대 경로 필수
    if not os.path.exists(weather_csv):
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Weather data file missing (RE100/38.csv)")

    output_csv = os.path.join(temp_dir, "result.csv")
    result_data = []

    async with engine_lock:
        try:
            print(f">>> [Sim] Start: ID {building_id}")
            matlab_pkg.SNUsolar_dsm2irr(
                weather_csv, 
                matlab.double(37.6), 
                matlab.double(127.2), 
                matlab.double(129.0),
                dsm_path, 
                roof_mask, 
                matlab.logical(False), 
                matlab.double(0), 
                matlab.double(180), 
                output_csv
            )
            
            if os.path.exists(output_csv):
                df = pd.read_csv(output_csv)
                # JSON 변환 (NaN 처리)
                result_data = df.where(pd.notnull(df), None).to_dict(orient='records')
            else:
                raise Exception("Result CSV not created by MATLAB")

        except Exception as e:
            print(f">>> [Error] MATLAB Run Failed: {e}")
            raise HTTPException(status_code=500, detail=f"Simulation Engine Error: {str(e)}")
        finally:
            # 4. 청소 (매우 중요: 임시 파일 삭제)
            # 디버깅 중에는 아래 줄을 주석 처리 하세요.
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f">>> [Sim] Finished: ID {building_id}")

    return {
        "building_id": building_id,
        "buildings_in_radius": len(building_list),
        "status": "success",
        "results": result_data
    }

if __name__ == "__main__":
    import uvicorn
    # 워커는 반드시 1개여야 MATLAB 글로벌 변수 공유 가능
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)