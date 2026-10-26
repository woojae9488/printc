"""이미지를 색별 영역으로 바꿈.

좌표는 픽셀, 원점은 이미지 왼쪽 아래, y 위쪽 양수. 색 수만큼 색을 줄이고 색마다 영역을 만듦.
테두리와 닿은 색은 배경으로 보고 뺌. 크기 맞춤, 경로 변환은 호출자가 함.
simplify면 일러스트처럼 단순화함: 중간값 필터로 질감을 지우고, 색을 줄인 뒤 다수결로 잔 얼룩을 지움.
photo면 사진 보정을 더함: 조명 얼룩을 펴고, 배경과 다른 대상만 잘라 다시 줄이고,
대상 가장자리와 닿은 색을 모두 배경으로 봄.
photo 생략(자동)은 테두리가 한 색으로 균일할 때만 켬.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

FORMATS = ("PNG", "JPEG", "BMP", "GIF", "WEBP")
MAX_SOURCE_PIXELS = 20_000_000
# 윤곽을 따는 작업 해상도(긴 변). 큰 이미지는 줄이고 작은 이미지는 키워 계단을 줄임.
WORK_MAX_PX = 400
# 사진 보정에서 대상을 잘라 다시 줄이기 전까지 들고 있는 해상도(긴 변). 작은 이미지는 키우지 않음.
HI_MAX_PX = 1200
MAX_COLORS = 8
# 색마다 행별 잉크 구간 수 상한. 사진, 잔 무늬처럼 복잡한 이미지를 걸러냄.
MAX_RUNS = 20_000
# 계단 모양 경계를 펴는 단순화 허용 오차(픽셀). 대각 계단의 모서리 편차(약 0.71)보다 조금 큼.
SIMPLIFY_PX = 0.75
# 이보다 작은 조각, 구멍은 잡티로 보고 버림(픽셀²).
MIN_SPECK_PX2 = 6.0
# 단순화: 질감을 지우는 중간값 필터 크기, 잔 얼룩을 지우는 다수결 창 반지름과 횟수.
MEDIAN_SIZE = 5
SMOOTH_RADIUS = 2
SMOOTH_ROUNDS = 2
# 색 줄이기(k-means): 반복 상한, 표본 수, 시작 색 후보를 모으는 색 칸 크기와 후보가 될 최소 비율.
KMEANS_ITERS = 12
KMEANS_SAMPLE = 20_000
KMEANS_BIN = 16
MIN_BIN_RATIO = 0.005
# 색 거리는 Lab에서 재되 a, b에 가중을 주고 RGB 거리 규모로 맞춤(밝기가 같은 분홍과 회색을 가름).
LAB_WEIGHTS = (1.0, 2.0, 2.0)
LAB_SCALE = 2.55
# 이 Lab 거리(ΔE) 미만인 색은 같은 색으로 합침. 안티앨리어싱, JPEG 편차로 같은 색이 둘로 남는 것을 막음.
# 색을 나눌 때의 a, b 가중은 합치기에 쓰지 않음.
MERGE_DELTA_E = 12.0
# 그릴 전체 면적 대비 이 비율보다 작은 색은 버림.
MIN_LAYER_RATIO = 0.01
# 색 영역의 평균 폭(2 × 면적 / 둘레, 픽셀)이 이보다 가늘면 버림. 경계의 안티앨리어싱 띠가 색으로 남는 것을 막음.
MIN_LAYER_WIDTH_PX = 3.5
# 조명으로 나눌 때 어두운 배경에서 0에 가까운 값으로 나누지 않게 하는 하한(0–255).
LIGHT_FLOOR = 16.0
# 색이 하나일 때 나누는 군집 수(배경 몫 포함).
SILHOUETTE_CLUSTERS = 3
# 사진 보정: 테두리 띠 비율, 자동 판정에 쓰는 테두리 균일도(Lab 거리 p90 상한).
BORDER_FRACTION = 0.08
UNIFORM_BORDER_DE = 10.0
# 대상 찾기: 배경과 Lab 거리가 max(최소 거리, 잡음 배수 × 테두리 p90)보다 먼 픽셀을 대상으로 봄.
SUBJECT_MIN_DE = 15.0
SUBJECT_NOISE_FACTOR = 3.0
SUBJECT_CLOSE_PX = 5
# 가장 큰 덩어리의 이 비율 미만인 덩어리(접힌 선, 먼지)는 대상에서 뺌. 대상이 이미지 대부분이면 자르지 않음.
SUBJECT_PART_RATIO = 0.02
SUBJECT_MAX_AREA_RATIO = 0.9
SUBJECT_MARGIN = 0.03
CROP_PAD_PX = 1
# 대상 가장자리 띠 폭(픽셀)과, 이 비율 이상 차지한 색을 배경으로 보는 기준.
EDGE_RING_PX = 3
EDGE_BACKGROUND_SHARE = 0.10


class ImageRegionError(Exception):
    """사용자에게 보일 이미지 오류."""


@dataclass(frozen=True)
class ColorLayer:
    color: tuple[int, int, int]
    region: BaseGeometry

    @property
    def hex(self) -> str:
        return "#%02x%02x%02x" % self.color


@dataclass(frozen=True)
class ImageRegions:
    """색별 영역. 영역은 모두 같은 원점(그릴 전체의 외곽 사각형 중심)과 배율을 씀.

    side는 외곽 사각형의 긴 변, reach는 원점에서 가장 먼 점까지(픽셀). layers는 면적이 큰 순서.
    photo는 사진 보정을 썼는지.
    """

    layers: list[ColorLayer]
    side: float
    reach: float
    photo: bool = False

    def parts(self, index: int, size: float) -> list[Polygon]:
        """index번 색 조각들. 전체 긴 변이 size(mm)가 되게 맞춤. 읽는 순서(위에서 아래, 왼쪽에서 오른쪽)."""
        scale = size / self.side
        scaled = affinity.scale(self.layers[index].region, scale, scale, origin=(0, 0))
        return sorted(_polygons(scaled), key=lambda poly: (-round(poly.centroid.y, 3), poly.centroid.x))


# ---------- 읽기, 크기 ----------

def read_rgb(path: Path) -> Image.Image:
    """RGB 이미지. 투명한 곳은 흰색으로 봄. 긴 변이 HI_MAX_PX보다 크면 줄임."""
    try:
        with Image.open(path, formats=FORMATS) as img:
            if img.width * img.height > MAX_SOURCE_PIXELS:
                raise ImageRegionError(f"이미지가 너무 큼: {img.width}x{img.height}")
            img = ImageOps.exif_transpose(img).convert("RGBA")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        raise ImageRegionError(f"이미지 읽기 실패: {path}: {exc}") from exc
    page = Image.new("RGBA", img.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(page, img).convert("RGB")
    return fit_long_side(rgb, HI_MAX_PX, enlarge=False)


def fit_long_side(img: Image.Image, px: int, enlarge: bool) -> Image.Image:
    ratio = px / max(img.size)
    if ratio == 1 or (ratio > 1 and not enlarge):
        return img
    size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    return img.resize(size, Image.Resampling.LANCZOS)


# ---------- 색 공간, 조명 ----------

def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(…, 3) 0–255 sRGB → CIE Lab(D65)."""
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    matrix = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ matrix.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def _border_band(height: int, width: int) -> np.ndarray:
    band = np.zeros((height, width), dtype=bool)
    edge = max(2, int(BORDER_FRACTION * min(height, width)))
    band[:edge] = band[-edge:] = True
    band[:, :edge] = band[:, -edge:] = True
    return band


def _terms(width: int, height: int) -> list[np.ndarray]:
    """2차 곡면의 항. 좌표는 이미지 크기에 상관없이 -0.5–0.5로 맞춤."""
    x = (np.arange(width) / width - 0.5)[None, :]
    y = (np.arange(height) / height - 0.5)[:, None]
    one = np.ones((height, width))
    return [one, one * x, one * y, one * x * x, one * x * y, one * y * y]


def border_noise(rgb: np.ndarray) -> float:
    """테두리 띠 색이 중앙값 색에서 벗어난 정도(Lab 거리 p90). 작을수록 배경이 균일함."""
    lab = srgb_to_lab(rgb.astype(np.float64))[_border_band(*rgb.shape[:2])]
    return float(np.percentile(np.linalg.norm(lab - np.median(lab, axis=0), axis=1), 90))


def fit_light(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """테두리 띠(배경)로 맞춘 2차 곡면 계수(채널마다)와, 곡면의 대표 밝기."""
    height, width = rgb.shape[:2]
    band = _border_band(height, width)
    terms = _terms(width, height)
    basis = np.stack([t[band] for t in terms], axis=-1)
    coefs = np.stack([np.linalg.lstsq(basis, rgb[..., i][band].astype(np.float64), rcond=None)[0] for i in range(3)])
    return coefs, np.median(_light(coefs, width, height).reshape(-1, 3), axis=0)


def _light(coefs: np.ndarray, width: int, height: int) -> np.ndarray:
    terms = _terms(width, height)
    return np.stack([sum(c * t for c, t in zip(channel, terms)) for channel in coefs], axis=-1)


def flatten(rgb: np.ndarray, coefs: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """조명 곡면으로 나눠 배경이 한 밝기가 되게 함. 같은 계수를 어느 해상도에도 씀."""
    light = np.maximum(_light(coefs, rgb.shape[1], rgb.shape[0]), LIGHT_FLOOR)
    return np.clip(rgb / light * np.maximum(reference, LIGHT_FLOOR), 0, 255).astype(np.uint8)


# ---------- 대상 찾기 ----------

def subject_shell(rgb: np.ndarray) -> Polygon | None:
    """배경과 색이 다른 큰 덩어리들의 볼록 껍질을 조금 넓힌 영역. 못 찾거나 이미지 대부분이면 None.

    접힌 선, 먼지, 그림자는 덩어리 크기와 닫기로 빠짐. 좌표는 y 위쪽 양수.
    """
    height, width = rgb.shape[:2]
    lab = srgb_to_lab(rgb.astype(np.float64))
    band = lab[_border_band(height, width)]
    background = np.median(band, axis=0)
    spread = float(np.percentile(np.linalg.norm(band - background, axis=1), 90))
    far = np.linalg.norm(lab - background, axis=-1) > max(SUBJECT_MIN_DE, SUBJECT_NOISE_FACTOR * spread)
    img = Image.fromarray((far * 255).astype(np.uint8))
    size = SUBJECT_CLOSE_PX
    mask = np.asarray(img.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))) > 127
    if not mask.any():
        return None
    try:
        parts = sorted(_polygons(mask_region(mask)), key=lambda poly: -poly.area)
    except ImageRegionError:
        return None
    if not parts:
        return None
    hull = shapely.union_all([p for p in parts if p.area >= SUBJECT_PART_RATIO * parts[0].area]).convex_hull
    x0, y0, x1, y1 = hull.bounds
    shell = hull.buffer(SUBJECT_MARGIN * max(x1 - x0, y1 - y0)).intersection(shapely.box(0, 0, width, height))
    if shell.is_empty or not isinstance(shell, Polygon) or shell.area > SUBJECT_MAX_AREA_RATIO * width * height:
        return None
    return shell


def _crop_subject(hi: Image.Image, shell: Polygon, lo_width: int) -> tuple[np.ndarray, np.ndarray]:
    """고해상도에서 대상 외곽 사각형만 잘라 작업 해상도로 줄임. 반환: RGB, 대상 안쪽 마스크."""
    ratio = hi.width / lo_width
    x0, y0, x1, y1 = (v * ratio for v in shell.bounds)
    # 작은 원본에서 반올림으로 대상이 가장자리에 붙지 않게 바깥으로 올림하고 한 픽셀 더 둠.
    pad = CROP_PAD_PX
    box = (max(0, math.floor(x0) - pad), max(0, math.floor(hi.height - y1) - pad),
           min(hi.width, math.ceil(x1) + pad), min(hi.height, math.ceil(hi.height - y0) + pad))
    crop = fit_long_side(hi.crop(box), WORK_MAX_PX, enlarge=True)
    scale = crop.width / (box[2] - box[0])
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).polygon([((x * ratio - box[0]) * scale, (hi.height - y * ratio - box[1]) * scale)
                                  for x, y in shell.exterior.coords], fill=255)
    return np.asarray(crop), np.asarray(mask) > 127


# ---------- 색 나누기 ----------

def _distances(points: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)


def _features(rgb: np.ndarray) -> np.ndarray:
    lab = srgb_to_lab(rgb.reshape(-1, 3).astype(np.float64))
    return lab * np.array(LAB_WEIGHTS) * LAB_SCALE


def _seeds(sample: np.ndarray, k: int) -> np.ndarray:
    """시작 색 k개. 색 칸별 평균 중 충분히 많은 칸만 후보로 삼아 가장 많은 색에서 가장 먼 색을 차례로 고름.

    가장자리가 섞인 드문 색이 시작 색이 되는 것을 막음.
    """
    bins = ((sample - sample.min(axis=0)) // KMEANS_BIN).astype(np.int64)
    keys = (bins[:, 0] * 1_000 + bins[:, 1]) * 1_000 + bins[:, 2]
    values, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    means = np.array([sample[inverse == i].mean(axis=0) for i in range(len(values))])
    common = counts >= MIN_BIN_RATIO * len(sample)
    if common.sum() < k:
        common = np.ones(len(values), dtype=bool)
    means, counts = means[common], counts[common]
    seeds = [means[np.argmax(counts)]]
    while len(seeds) < k:
        far = _distances(means, np.array(seeds)).min(axis=1)
        seeds.append(means[np.argmax(far)])
    return np.array(seeds)


def kmeans_centers(points: np.ndarray, k: int) -> np.ndarray:
    """색 k개의 중심. 시작 색이 정해져 있어 결과가 늘 같음."""
    step = max(1, len(points) // KMEANS_SAMPLE)
    sample = points[::step].astype(np.float64)
    result = _seeds(sample, k)
    for _ in range(KMEANS_ITERS):
        nearest = _distances(sample, result).argmin(axis=1)
        moved = np.array([sample[nearest == c].mean(axis=0) if (nearest == c).any() else result[c]
                          for c in range(k)])
        if np.allclose(moved, result):
            break
        result = moved
    return result


def _merge_close(labels: np.ndarray, centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """가까운 색 둘을 합침. 적은 쪽이 많은 쪽으로 감. 더 합칠 것이 없을 때까지 반복. -1은 색이 아님.

    centers는 _features 공간의 값. 거리는 가중을 푼 Lab ΔE로 잼.
    """
    centers = centers.copy()
    plain = np.array(LAB_WEIGHTS) * LAB_SCALE
    counts = np.bincount(labels[labels >= 0], minlength=len(centers)).astype(float)
    alive = counts > 0
    while True:
        ids = np.flatnonzero(alive)
        best = None
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dist = float(np.linalg.norm((centers[a] - centers[b]) / plain))
                if dist < MERGE_DELTA_E and (best is None or dist < best[0]):
                    best = (dist, a, b)
        if best is None:
            return labels, centers
        _, a, b = best
        keep, drop = (a, b) if counts[a] >= counts[b] else (b, a)
        centers[keep] = (centers[keep] * counts[keep] + centers[drop] * counts[drop]) / (counts[keep] + counts[drop])
        counts[keep] += counts[drop]
        counts[drop] = 0
        alive[drop] = False
        labels = np.where(labels == drop, keep, labels)


def _majority(labels: np.ndarray, count: int) -> np.ndarray:
    """창 안에서 가장 많은 색으로 바꿈. 동률이면 원래 색을 유지함. -1(대상 밖)은 표를 못 얻고 그대로 둠."""
    height, width = labels.shape
    pad = np.pad(labels, SMOOTH_RADIUS, mode="edge")
    votes = np.zeros((count, height, width), dtype=np.int32)
    span = 2 * SMOOTH_RADIUS + 1
    for dy in range(span):
        for dx in range(span):
            window = pad[dy:dy + height, dx:dx + width]
            for c in range(count):
                votes[c] += window == c
    # 자기 색에 반 표를 더해 동률일 때 바뀌지 않게 함.
    votes = votes * 2 + (np.arange(count)[:, None, None] == labels[None])
    return np.where(labels >= 0, votes.argmax(axis=0), -1)


def _cluster(features: np.ndarray, valid: np.ndarray, count: int, simplify: bool) -> np.ndarray:
    """대상 안쪽 픽셀을 count색으로 나눈 라벨 배열. 대상 밖은 -1."""
    flat = valid.ravel()
    centers = kmeans_centers(features[flat], count)
    labels = np.full(len(features), -1, dtype=np.int64)
    labels[flat] = _distances(features[flat], centers).argmin(axis=1)
    labels, centers = _merge_close(labels.reshape(valid.shape), centers)
    if simplify:
        for _ in range(SMOOTH_ROUNDS):
            labels = _majority(labels, len(centers))
    return labels


def _edge_ring(valid: np.ndarray) -> np.ndarray:
    """대상 가장자리 띠. 이미지 전체가 대상이면 이미지 테두리 한 줄."""
    if valid.all():
        ring = np.zeros_like(valid)
        ring[0] = ring[-1] = True
        ring[:, 0] = ring[:, -1] = True
        return ring
    eroded = Image.fromarray(valid.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(2 * EDGE_RING_PX + 1))
    inner = np.asarray(eroded)
    return valid & (inner <= 127)


def background_labels(labels: np.ndarray, valid: np.ndarray, photo: bool) -> set[int]:
    """가장자리 띠와 닿은 색. 사진은 이 비율 이상 차지한 색 전부(천, 스티커 바탕), 아니면 가장 많은 색 하나."""
    ring = labels[_edge_ring(valid)]
    ring = ring[ring >= 0]
    shares = np.bincount(ring, minlength=int(labels.max()) + 1) / len(ring)
    found = {int(i) for i in np.flatnonzero(shares >= EDGE_BACKGROUND_SHARE)} if photo else set()
    return found or {int(shares.argmax())}


def quantize(rgb: np.ndarray, colors: int, simplify: bool, draw_background: bool, valid: np.ndarray | None = None,
             photo: bool = False) -> tuple[np.ndarray, set[int]]:
    """색 라벨 배열(대상 밖은 -1)과 뺄 배경 라벨. 배경 몫으로 색을 더 만듦.

    사진은 배경이 둘 이상(천과 스티커 바탕)일 수 있어 개수를 알면 한 번 더 나눠 그릴 색 수를 맞춤.
    draw_background면 사진은 배경 중 면적이 가장 큰 색만 그리고, 아니면 배경을 빼지 않음.
    """
    valid = np.ones(rgb.shape[:2], dtype=bool) if valid is None else valid
    features = _features(rgb)
    # 한 색이면 배경과 가까운 진한 색이 배경 군집에 먹히지 않게 넉넉히 나눈 뒤 배경이 아닌 것을 한 색으로 묶음.
    spare = SILHOUETTE_CLUSTERS - 1 if colors == 1 else colors
    count = spare if draw_background and not photo else spare + 1
    labels = _cluster(features, valid, count, simplify)
    if not photo:
        background = set() if draw_background else background_labels(labels, valid, False)
    else:
        background = background_labels(labels, valid, True)
        needed = max(2, spare + len(background) - (1 if draw_background else 0))
        if needed != count:
            labels = _cluster(features, valid, needed, simplify)
            background = background_labels(labels, valid, True)
        if draw_background:
            area = np.bincount(labels[labels >= 0], minlength=int(labels.max()) + 1)
            background.discard(max(background, key=lambda label: area[label]))
    if colors == 1:
        drawn = (labels >= 0) & ~np.isin(labels, list(background))
        if drawn.any():
            labels = np.where(drawn, labels[drawn].min(), labels)
    return labels, background


# ---------- 영역 ----------

def _polygons(geom: BaseGeometry) -> list[Polygon]:
    return list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom] if isinstance(geom, Polygon) else []


def _clean(geom: BaseGeometry) -> list[Polygon]:
    """잡티 조각과 구멍을 버림."""
    result = []
    for poly in _polygons(geom):
        if poly.area >= MIN_SPECK_PX2:
            holes = [h for h in poly.interiors if Polygon(h).area >= MIN_SPECK_PX2]
            result.append(Polygon(poly.exterior, holes))
    return result


def mask_region(mask: np.ndarray) -> BaseGeometry:
    """잉크 픽셀을 다각형으로 묶음. 행마다 이어진 구간을 사각형으로 만들어 합친 뒤 계단을 폄."""
    height = mask.shape[0]
    edges = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    rows, starts = np.nonzero(edges == 1)
    _, ends = np.nonzero(edges == -1)
    if len(rows) > MAX_RUNS:
        raise ImageRegionError(f"이미지가 너무 복잡함(잉크 구간 {len(rows)}개 > {MAX_RUNS}): 단순한 그림 사용")
    boxes = shapely.box(starts, height - rows - 1, ends, height - rows)
    polys = _clean(shapely.union_all(boxes).simplify(SIMPLIFY_PX, preserve_topology=True))
    return MultiPolygon(polys)


def _mean_width(region: BaseGeometry) -> float:
    return 2 * region.area / region.length


def prepare(path: Path, simplify: bool, photo: bool | None) -> tuple[np.ndarray, np.ndarray | None, bool]:
    """작업 해상도 RGB와 대상 마스크(자르지 않았으면 None), 사진 보정을 썼는지."""
    hi = read_rgb(path)
    lo = fit_long_side(hi, WORK_MAX_PX, enlarge=True)
    rgb, valid, use_photo = np.asarray(lo), None, False
    if photo is not False:
        coefs, reference = fit_light(rgb)
        flat = flatten(rgb, coefs, reference)
        # 조명을 편 뒤에도 테두리가 고르지 않으면(배경이 한 색이 아니면) 사진 보정을 쓰지 않음.
        shell = subject_shell(flat) if photo or border_noise(flat) <= UNIFORM_BORDER_DE else None
        # 대상을 못 찾으면(이미지 대부분이 대상) 보정하지 않음. 테두리에 닿은 대상이 배경으로 빠지는 것을 막음.
        if shell is not None:
            hi = Image.fromarray(flatten(np.asarray(hi), coefs, reference))
            rgb, valid = _crop_subject(hi, shell, lo.width)
            use_photo = True
    if simplify:
        rgb = np.asarray(Image.fromarray(rgb).filter(ImageFilter.MedianFilter(MEDIAN_SIZE)))
    return rgb, valid, use_photo


def load_regions(path: Path, colors: int, simplify: bool = True, draw_background: bool = False,
                 photo: bool | None = None) -> ImageRegions:
    """이미지 파일의 색별 영역. 면적이 큰 색부터."""
    if not 1 <= colors <= MAX_COLORS:
        raise ImageRegionError(f"색 수는 1–{MAX_COLORS}")
    rgb, valid, use_photo = prepare(path, simplify, photo)
    valid_or_all = np.ones(rgb.shape[:2], dtype=bool) if valid is None else valid
    labels, background = quantize(rgb, colors, simplify, draw_background, valid_or_all, use_photo)
    found = []
    for label in np.unique(labels):
        if label < 0 or label in background:
            continue
        region = mask_region(labels == label)
        if not region.is_empty:
            found.append((tuple(int(v) for v in rgb[labels == label].mean(axis=0).round()), region))
    found = [item for item in found if _mean_width(item[1]) >= MIN_LAYER_WIDTH_PX]
    total = sum(region.area for _, region in found)
    found = sorted((item for item in found if item[1].area >= MIN_LAYER_RATIO * total),
                   key=lambda item: -item[1].area)
    if not found:
        raise ImageRegionError("그릴 윤곽이 없음: --colors, --draw-background, --photo 검토")
    xs0, ys0, xs1, ys1 = zip(*(region.bounds for _, region in found))
    x0, y0, x1, y1 = min(xs0), min(ys0), max(xs1), max(ys1)
    layers = [ColorLayer(color, affinity.translate(region, -(x0 + x1) / 2, -(y0 + y1) / 2)) for color, region in found]
    reach = max(float(np.hypot(*shapely.get_coordinates(layer.region).T).max()) for layer in layers)
    return ImageRegions(layers, max(x1 - x0, y1 - y0), reach, use_photo)
