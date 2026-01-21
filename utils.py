import numpy as np
import cv2
from shapely import wkb
from shapely.ops import transform
import pyproj
import os

# DB(WGS84, EPSG:4326) -> 미터좌표(UTM-K, EPSG:5179) 변환기
# 한국 기준 가장 정확한 거리 계산을 위해 5179 사용
# transformer = pyproj.Transformer.from_crs("epsg:4326", "epsg:5179", always_xy=True).transform

def create_simulation_inputs(target_geom_wkb, neighbor_list, output_dir, file_prefix):
    CANVAS_SIZE = 1000   # 1000x1000 픽셀
    PIXEL_PER_METER = 1.0

    # 1) 타겟 건물 중심점 (DB가 5179이므로 변환 없이 그대로)
    target_poly = wkb.loads(bytes.fromhex(target_geom_wkb) if isinstance(target_geom_wkb, str) else target_geom_wkb)
    center_x, center_y = target_poly.centroid.x, target_poly.centroid.y

    # 2) 캔버스 초기화
    dsm = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.float64)
    mask_roof = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.uint8)

    # 3) 픽셀 좌표 변환 함수 (클리핑 포함 권장)
    def to_pixel_coords(geom_5179):
        boundaries = []
        if geom_5179.geom_type == "Polygon":
            boundaries = [geom_5179.exterior.coords]
        elif geom_5179.geom_type == "MultiPolygon":
            boundaries = [p.exterior.coords for p in geom_5179.geoms]
        else:
            return []

        pixel_polys = []
        for coords in boundaries:
            pts = []
            for x, y in coords:
                px = int((x - center_x) * PIXEL_PER_METER + (CANVAS_SIZE / 2))
                py = int((CANVAS_SIZE / 2) - (y - center_y) * PIXEL_PER_METER)
                pts.append([px, py])

            arr = np.array(pts, np.int32)

            # 캔버스 밖 완전 이탈한 폴리곤은 스킵 (옵션)
            if (arr[:, 0] < 0).all() or (arr[:, 0] >= CANVAS_SIZE).all() or (arr[:, 1] < 0).all() or (arr[:, 1] >= CANVAS_SIZE).all():
                continue

            pixel_polys.append(arr)
        return pixel_polys

    # 4) 그리기
    for building in neighbor_list:
        b_wkb = building["geom"]
        poly = wkb.loads(bytes.fromhex(b_wkb) if isinstance(b_wkb, str) else b_wkb)

        pixel_polys = to_pixel_coords(poly)
        if not pixel_polys:
            continue

        cv2.fillPoly(dsm, pixel_polys, color=float(building["height"]))

        if building["is_target"]:
            cv2.fillPoly(mask_roof, pixel_polys, color=1)

    # 5) 저장
    os.makedirs(output_dir, exist_ok=True)
    path_dsm = os.path.join(output_dir, f"{file_prefix}_floco.npy")
    path_roof = os.path.join(output_dir, f"{file_prefix}_rm_roof.npy")
    path_facade = os.path.join(output_dir, f"{file_prefix}_rm_facade.npy")

    np.save(path_dsm, dsm)
    np.save(path_roof, mask_roof)
    np.save(path_facade, mask_roof)

    return path_dsm, path_roof, path_facade


# def create_simulation_inputs(target_geom_wkb, neighbor_list, output_dir, file_prefix):
#     CANVAS_SIZE = 1000   # 1000x1000 픽셀
#     PIXEL_PER_METER = 1.0 
    
#     # 1. 타겟 건물 중심점 계산 (앵커 포인트)
#     target_poly = wkb.loads(bytes.fromhex(target_geom_wkb) if isinstance(target_geom_wkb, str) else target_geom_wkb)
#     target_meter = transform(transformer, target_poly)
#     center_x, center_y = target_meter.centroid.x, target_meter.centroid.y

#     # 2. 캔버스 초기화
#     dsm = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.float64)
#     mask_roof = np.zeros((CANVAS_SIZE, CANVAS_SIZE), dtype=np.uint8)
    
#     # 3. 그리기
#     for building in neighbor_list:
#         b_wkb = building['geom']
#         poly_geo = wkb.loads(bytes.fromhex(b_wkb) if isinstance(b_wkb, str) else b_wkb)
#         poly_meter = transform(transformer, poly_geo)
        
#         # 픽셀 좌표 변환 함수
#         def to_pixel_coords(geom_meter):
#             boundaries = []
#             if geom_meter.geom_type == 'Polygon':
#                 boundaries = [geom_meter.exterior.coords]
#             elif geom_meter.geom_type == 'MultiPolygon':
#                 boundaries = [p.exterior.coords for p in geom_meter.geoms]
            
#             pixel_polys = []
#             for coords in boundaries:
#                 pts = []
#                 for x, y in coords:
#                     # 캔버스 중앙(500,500)에 타겟 중심 배치
#                     px = int((x - center_x) * PIXEL_PER_METER + (CANVAS_SIZE / 2))
#                     py = int((CANVAS_SIZE / 2) - (y - center_y) * PIXEL_PER_METER) # Y축 반전
#                     pts.append([px, py])
#                 pixel_polys.append(np.array(pts, np.int32))
#             return pixel_polys

#         pixel_polys = to_pixel_coords(poly_meter)
#         if not pixel_polys: continue

#         # DSM (모든 건물)
#         cv2.fillPoly(dsm, pixel_polys, color=building['height'])
        
#         # Roof Mask (타겟 건물만)
#         if building['is_target']:
#             cv2.fillPoly(mask_roof, pixel_polys, color=1)

#     # 4. 저장
#     os.makedirs(output_dir, exist_ok=True)
#     path_dsm = os.path.join(output_dir, f"{file_prefix}_floco.npy")
#     path_roof = os.path.join(output_dir, f"{file_prefix}_rm_roof.npy")
#     path_facade = os.path.join(output_dir, f"{file_prefix}_rm_facade.npy") # 외벽도 지붕 마스크 공유

#     np.save(path_dsm, dsm)
#     np.save(path_roof, mask_roof)
#     np.save(path_facade, mask_roof) 

#     return path_dsm, path_roof, path_facade