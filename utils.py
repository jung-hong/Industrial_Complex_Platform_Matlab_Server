# utils.py
import numpy as np
import cv2
from shapely import wkb
from shapely.ops import transform
from shapely.geometry import Polygon, MultiPolygon
import pyproj
import os

# 좌표 변환기 설정
# DB(4326: 위경도) -> 미터 좌표계(5179: Korea 2000 / UTM-K)
# 만약 DB가 이미 TM좌표라면 source_crs를 5174 등으로 변경해야 함
transformer = pyproj.Transformer.from_crs("epsg:4326", "epsg:5179", always_xy=True).transform

def create_simulation_inputs(target_geom_wkb, neighbor_list, output_dir, file_prefix):
    """
    타겟 건물과 주변 건물을 받아 MATLAB 시뮬레이션용 NPY 파일을 생성함.
    :param target_geom_wkb: 타겟 건물의 기하정보 (중심점 계산용)
    :param neighbor_list: [{'geom': wkb, 'height': float, 'is_target': bool}, ...]
    :return: (dsm_path, roof_mask_path, facade_mask_path)
    """
    CANVAS_SIZE = 1000   # 1000 x 1000 픽셀
    PIXEL_PER_METER = 1.0 # 1픽셀 = 1미터
    
    # 1. 타겟 건물의 중심점(Anchor) 계산
    # 이 점이 캔버스의 (500, 500) 위치가 됩니다.
    target_poly = wkb.loads(bytes.fromhex(target_geom_wkb) if isinstance(target_geom_wkb, str) else target_geom_wkb)
    target_meter = transform(transformer, target_poly) # 미터 좌표로 변환
    center_x, center_y = target_meter.centroid.x, target_meter.centroid.y

    # 2. 캔버스 초기화 (0으로 채움)
    dsm = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.float64)       # 높이 맵
    mask_roof = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.uint8)   # 지붕 마스크
    
    # 3. 주변 건물 리스트를 순회하며 그리기
    for building in neighbor_list:
        # DB에서 가져온 WKB를 Shapely 객체로 변환
        b_wkb = building['geom']
        poly_geo = wkb.loads(bytes.fromhex(b_wkb) if isinstance(b_wkb, str) else b_wkb)
        
        # 미터 좌표계로 변환
        poly_meter = transform(transformer, poly_geo)
        
        # 캔버스(이미지) 좌표계로 변환하는 함수
        def to_pixel_coords(geom_meter):
            coords_list = []
            # Polygon이든 MultiPolygon이든 외곽선 추출
            if geom_meter.geom_type == 'Polygon':
                boundaries = [geom_meter.exterior.coords]
            elif geom_meter.geom_type == 'MultiPolygon':
                boundaries = [p.exterior.coords for p in geom_meter.geoms]
            else:
                return []

            pixel_polys = []
            for coords in boundaries:
                pts = []
                for x, y in coords:
                    # 중심점 기준 상대 좌표 계산 + 캔버스 중앙 이동
                    px = int((x - center_x) * PIXEL_PER_METER + (CANVAS_SIZE / 2))
                    # 이미지는 y축이 아래로 내려갈수록 증가하므로 뒤집기 (반전)
                    py = int((CANVAS_SIZE / 2) - (y - center_y) * PIXEL_PER_METER)
                    pts.append([px, py])
                pixel_polys.append(np.array(pts, np.int32))
            return pixel_polys

        pixel_polys = to_pixel_coords(poly_meter)
        
        if not pixel_polys:
            continue

        # A. DSM 그리기 (건물 높이로 색칠) -> 주변 건물 그림자 효과용
        cv2.fillPoly(dsm, pixel_polys, color=building['height'])
        
        # B. 타겟 마스크 그리기 (1로 색칠) -> 분석 대상 영역
        if building['is_target']:
            cv2.fillPoly(mask_roof, pixel_polys, color=1)

    # 4. 파일 저장
    os.makedirs(output_dir, exist_ok=True)
    
    path_dsm = os.path.join(output_dir, f"{file_prefix}_floco.npy")
    path_roof = os.path.join(output_dir, f"{file_prefix}_rm_roof.npy")
    path_facade = os.path.join(output_dir, f"{file_prefix}_rm_facade.npy") # 외벽도 지붕과 같은 영역으로 가정

    np.save(path_dsm, dsm)
    np.save(path_roof, mask_roof)
    np.save(path_facade, mask_roof) # 외벽 마스크도 일단 동일하게 저장

    return path_dsm, path_roof, path_facade