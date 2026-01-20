# main.py
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import asyncio
import os
import uuid
import pandas as pd
import shutil

# 만든 모듈 임포트
from database import get_db
from utils import create_simulation_inputs

# MATLAB 관련 (Windows 서버 환경 가정)
# Mac에서 개발중이라면 try-except로 가짜 모듈 처리 필요
try:
    import matlab
    import SNUs_dsm2irrPkg
    MATLAB_AVAILABLE = True
except ImportError:
    print("Warning: MATLAB Runtime not found. Using Mock mode.")
    MATLAB_AVAILABLE = False
    matlab = None

app = FastAPI()

# MATLAB 엔진 전역 변수 & 락
matlab_pkg = None
engine_lock = asyncio.Lock()

@app.on_event("startup")
def startup_event():
    global matlab_pkg
    if MATLAB_AVAILABLE:
        print(">>> Initializing MATLAB Runtime...")
        try:
            matlab_pkg = SNUs_dsm2irrPkg.initialize()
            print(">>> MATLAB Runtime initialized.")
        except Exception as e:
            print(f">>> Initialization Failed: {e}")

@app.on_event("shutdown")
def shutdown_event():
    global matlab_pkg
    if matlab_pkg:
        matlab_pkg.terminate()

@app.post("/simulate/{building_id}")
async def run_simulation(building_id: int, db: Session = Depends(get_db)):
    """
    건물 ID를 받아 주변 건물 정보를 포함한 NPY를 생성하고 시뮬레이션을 돌림
    """
    global matlab_pkg
    
    if MATLAB_AVAILABLE and not matlab_pkg:
        raise HTTPException(status_code=500, detail="MATLAB engine not ready")

    # =========================================================
    # 1. DB 조회: 타겟 건물 + 주변 건물 (반경 700m)
    # =========================================================
    
    # 1-1. 타겟 건물의 기하 정보 먼저 확보 (중심점용)
    target_query = text("SELECT geom FROM building_gis WHERE id = :bid")
    target_res = db.execute(target_query, {"bid": building_id}).fetchone()
    
    if not target_res:
        raise HTTPException(status_code=404, detail="Target building not found")
    
    target_geom_raw = target_res[0] # WKB 형태

    # 1-2. 주변 건물 검색 (PostGIS ST_DWithin 사용)
    # 캔버스가 1000m x 1000m 이므로 반경 500m 이상(안전하게 700m) 검색
    # geom이 4326(위경도)라면 ::geography 캐스팅 필요
    neighbors_query = text("""
        SELECT 
            id, 
            geom, 
            COALESCE(gro_flo_co, 1) as floors 
        FROM building_gis 
        WHERE ST_DWithin(
            geom::geography, 
            (SELECT geom::geography FROM building_gis WHERE id = :bid), 
            700
        )
    """)
    
    neighbors = db.execute(neighbors_query, {"bid": building_id}).fetchall()
    
    # Python 리스트로 가공
    building_list = []
    for row in neighbors:
        bid, bgeom, floors = row
        
        # 높이 계산 (층수 * 3.3m)
        height = float(floors) * 3.3
        if height <= 0: height = 3.3 # 최소 높이 보정
        
        building_list.append({
            "geom": bgeom,
            "height": height,
            "is_target": (bid == building_id) # 타겟 건물 여부 체크
        })

    # =========================================================
    # 2. 전처리: NPY 파일 생성 (utils.py 위임)
    # =========================================================
    req_uuid = str(uuid.uuid4())
    temp_dir = os.path.abspath(os.path.join("temp", req_uuid))
    
    try:
        # 여기서 1픽셀=1미터 변환 및 파일 생성이 수행됨
        dsm_path, roof_mask, facade_mask = create_simulation_inputs(
            target_geom_raw, building_list, temp_dir, f"sample_{req_uuid}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preprocessing failed: {str(e)}")

    # =========================================================
    # 3. MATLAB 시뮬레이션 실행
    # =========================================================
    
    # 날씨 파일 (RE100 폴더 내 38.csv)
    weather_csv = os.path.abspath("RE100/38.csv")
    if not os.path.exists(weather_csv):
         raise HTTPException(status_code=500, detail="Weather file not found")

    output_csv = os.path.join(temp_dir, "result_roof.csv")
    results = {}

    if MATLAB_AVAILABLE:
        async with engine_lock:
            try:
                # MATLAB 변수 캐스팅
                tmydir = weather_csv
                tmylat = matlab.double(37.6)
                tmylon = matlab.double(127.2)
                tmyele = matlab.double(129.0)
                
                dsmdir = dsm_path
                regionmapdir = roof_mask
                
                print(f">>> Running Simulation for ID {building_id}...")
                
                # 지붕 계산 호출
                matlab_pkg.SNUsolar_dsm2irr(
                    tmydir, tmylat, tmylon, tmyele, 
                    dsmdir, regionmapdir, 
                    matlab.logical(False), # isfacade=False
                    matlab.double(0),      # pzen
                    matlab.double(180),    # pazi
                    output_csv
                )
                
                # 결과 읽기
                if os.path.exists(output_csv):
                    df = pd.read_csv(output_csv)
                    # JSON 직렬화를 위해 NaN을 None으로
                    results['roof'] = df.where(pd.notnull(df), None).to_dict(orient='records')
                else:
                    results['error'] = "Output CSV was not generated."

            except Exception as e:
                print(f"MATLAB Error: {e}")
                raise HTTPException(status_code=500, detail=f"Simulation Error: {str(e)}")
    else:
        # Mac 개발 환경용 Mock Data
        results['roof'] = [{"mock_data": "True", "radiation": 123.45}]
        print("!!! Mock Simulation Finished !!!")

    # =========================================================
    # 4. 마무리 및 리턴
    # =========================================================
    
    # 임시 파일 삭제 (디버깅용으로 주석 처리 가능)
    # try:
    #     shutil.rmtree(temp_dir)
    # except:
    #     pass

    return {
        "building_id": building_id,
        "total_buildings_processed": len(building_list),
        "status": "success",
        "data": results
    }

if __name__ == "__main__":
    import uvicorn
    # RE100 폴더가 있는지 꼭 확인!
    uvicorn.run(app, host="0.0.0.0", port=8000)