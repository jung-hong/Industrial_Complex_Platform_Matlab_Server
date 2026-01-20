# utils.py
import numpy as np
import cv2
from shapely import wkb
from shapely.geometry import Polygon
import os

def process_building_geometry(geom_wkb, floor_count, output_dir, file_prefix):
    """
    DB의 WKB 지오메트리를 받아서 NPY 파일(DSM, 마스크)을 생성
    """
    # 1. 층수 처리 (NULL이면 1층, 층고 3.3m)
    if floor_count is None or floor_count == 0:
        floors = 1
    else:
        floors = floor_count
    building_height = floors * 3.3

    # 2. WKB -> Shapely Polygon 변환
    polygon = wkb.loads(bytes.fromhex(geom_wkb) if isinstance(geom_wkb, str) else geom_wkb)
    
    # 3. 좌표 정규화 (건물을 1000x1000 캔버스 중앙에 배치)
    # 실제 시뮬레이션에서는 주변 지형도 중요하겠지만, 
    # 현재는 단일 건물의 형상과 높이만 고려하여 중앙에 배치합니다.
    minx, miny, maxx, maxy = polygon.bounds
    width = maxx - minx
    height = maxy - miny
    
    # 캔버스 설정 (1000x1000 픽셀)
    canvas_size = 1000
    scale = 1.0  # 1픽셀 = 1미터라고 가정 (필요시 조정)
    
    # 건물을 중앙으로 옮기기 위한 오프셋 계산
    offset_x = (canvas_size - width * scale) / 2 - minx * scale
    offset_y = (canvas_size - height * scale) / 2 - miny * scale

    # 좌표 변환 함수
    def transform_coords(coords):
        transformed = []
        for x, y in coords:
            tx = int(x * scale + offset_x)
            ty = int(y * scale + offset_y)
            transformed.append([tx, ty])
        return np.array(transformed, np.int32)

    # 폴리곤 외곽선 좌표 추출
    if isinstance(polygon, Polygon):
        pts = transform_coords(polygon.exterior.coords)
        polys = [pts]
    else: # MultiPolygon인 경우
        polys = []
        for p in polygon.geoms:
            polys.append(transform_coords(p.exterior.coords))

    # 4. NPY 데이터 생성
    # DSM: 바닥은 0, 건물 영역은 building_height 값
    dsm = np.zeros((canvas_size, canvas_size), dtype=np.float64)
    cv2.fillPoly(dsm, polys, color=building_height)

    # 지붕 마스크: 건물 영역은 1, 나머지는 0
    mask_roof = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    cv2.fillPoly(mask_roof, polys, color=1)
    
    # 외벽 마스크 (간단하게 외곽선만 1로 처리하거나, 로직에 따라 전체 채움)
    # 기존 코드 로직에 따라 일단 지붕과 동일하게 채우거나 별도 처리
    # 여기서는 편의상 동일 영역을 타겟으로 잡습니다.
    mask_facade = mask_roof.copy()

    # 5. 파일 저장
    os.makedirs(output_dir, exist_ok=True)
    
    path_dsm = os.path.join(output_dir, f"{file_prefix}_floco.npy")
    path_roof = os.path.join(output_dir, f"{file_prefix}_rm_roof.npy")
    path_facade = os.path.join(output_dir, f"{file_prefix}_rm_facade.npy")

    np.save(path_dsm, dsm)
    np.save(path_roof, mask_roof)
    np.save(path_facade, mask_facade)

    return path_dsm, path_roof, path_facade