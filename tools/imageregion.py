"""이미지를 색별 영역으로 바꿈.

좌표는 픽셀, 원점은 이미지 왼쪽 아래, y 위쪽 양수. 색 수만큼 색을 줄이고 색마다 영역을 만듦.
테두리와 가장 많이 닿은 색은 배경으로 보고 뺌. 크기 맞춤, 경로 변환은 호출자가 함.
simplify면 일러스트처럼 단순화함: 중간값 필터로 질감을 지우고, 색을 줄인 뒤 다수결로 잔 얼룩을 지움.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageFilter, ImageOps
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

FORMATS = ("PNG", "JPEG", "BMP", "GIF", "WEBP")
MAX_SOURCE_PIXELS = 20_000_000
# 윤곽을 따는 작업 해상도(긴 변). 큰 이미지는 줄이고 작은 이미지는 키워 계단을 줄임.
WORK_MAX_PX = 400
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
# RGB 거리가 이 미만인 색은 같은 색으로 합침. 안티앨리어싱 가장자리 색이 따로 남는 것을 막음.
MERGE_DISTANCE = 32.0
# 그릴 전체 면적 대비 이 비율보다 작은 색은 버림.
MIN_LAYER_RATIO = 0.01
# 색 영역의 평균 폭(2 × 면적 / 둘레, 픽셀)이 이보다 가늘면 버림. 경계의 안티앨리어싱 띠가 색으로 남는 것을 막음.
MIN_LAYER_WIDTH_PX = 4.0


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
    """

    layers: list[ColorLayer]
    side: float
    reach: float

    def parts(self, index: int, size: float) -> list[Polygon]:
        """index번 색 조각들. 전체 긴 변이 size(mm)가 되게 맞춤. 읽는 순서(위에서 아래, 왼쪽에서 오른쪽)."""
        scale = size / self.side
        scaled = affinity.scale(self.layers[index].region, scale, scale, origin=(0, 0))
        return sorted(_polygons(scaled), key=lambda poly: (-round(poly.centroid.y, 3), poly.centroid.x))


def load_rgb(path: Path, simplify: bool) -> np.ndarray:
    """RGB uint8 배열(행 0이 위). 투명한 곳은 흰색으로 봄. 긴 변을 WORK_MAX_PX로 맞춤."""
    try:
        with Image.open(path, formats=FORMATS) as img:
            if img.width * img.height > MAX_SOURCE_PIXELS:
                raise ImageRegionError(f"이미지가 너무 큼: {img.width}x{img.height}")
            img = ImageOps.exif_transpose(img).convert("RGBA")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        raise ImageRegionError(f"이미지 읽기 실패: {path}: {exc}") from exc
    page = Image.new("RGBA", img.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(page, img).convert("RGB")
    ratio = WORK_MAX_PX / max(rgb.size)
    rgb = rgb.resize((max(1, round(rgb.width * ratio)), max(1, round(rgb.height * ratio))),
                     Image.Resampling.LANCZOS)
    if simplify:
        rgb = rgb.filter(ImageFilter.MedianFilter(MEDIAN_SIZE))
    return np.asarray(rgb, dtype=np.uint8)


def _distances(points: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)


def _seeds(sample: np.ndarray, k: int) -> np.ndarray:
    """시작 색 k개. 색 칸별 평균 중 충분히 많은 칸만 후보로 삼아 가장 많은 색에서 가장 먼 색을 차례로 고름.

    가장자리가 섞인 드문 색이 시작 색이 되는 것을 막음.
    """
    bins = (sample // KMEANS_BIN).astype(np.int64)
    keys = bins[:, 0] * 10_000 + bins[:, 1] * 100 + bins[:, 2]
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


def kmeans_centers(pixels: np.ndarray, k: int) -> np.ndarray:
    """색 k개의 중심. 시작 색이 정해져 있어 결과가 늘 같음."""
    step = max(1, len(pixels) // KMEANS_SAMPLE)
    sample = pixels[::step].astype(np.float64)
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
    """가까운 색 둘을 합침. 적은 쪽이 많은 쪽으로 감. 더 합칠 것이 없을 때까지 반복."""
    centers = centers.copy()
    counts = np.bincount(labels.ravel(), minlength=len(centers)).astype(float)
    alive = counts > 0
    while True:
        ids = np.flatnonzero(alive)
        best = None
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dist = float(np.linalg.norm(centers[a] - centers[b]))
                if dist < MERGE_DISTANCE and (best is None or dist < best[0]):
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
    """창 안에서 가장 많은 색으로 바꿈. 동률이면 원래 색을 유지함."""
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
    return votes.argmax(axis=0)


def background_label(labels: np.ndarray) -> int:
    """이미지 테두리와 가장 많이 닿은 색."""
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return int(np.bincount(edge).argmax())


def quantize(rgb: np.ndarray, colors: int, simplify: bool, draw_background: bool) -> tuple[np.ndarray, int | None]:
    """색 라벨 배열과 배경 라벨(그리면 None). 배경을 빼면 배경 몫으로 색을 하나 더 만듦."""
    pixels = rgb.reshape(-1, 3)
    count = colors if draw_background else colors + 1
    centers = kmeans_centers(pixels, count)
    labels = _distances(pixels.astype(np.float64), centers).argmin(axis=1).reshape(rgb.shape[:2])
    labels, centers = _merge_close(labels, centers)
    if simplify:
        for _ in range(SMOOTH_ROUNDS):
            labels = _majority(labels, len(centers))
    return labels, None if draw_background else background_label(labels)


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


def load_regions(path: Path, colors: int, simplify: bool = True, draw_background: bool = False) -> ImageRegions:
    """이미지 파일의 색별 영역. 면적이 큰 색부터."""
    if not 1 <= colors <= MAX_COLORS:
        raise ImageRegionError(f"색 수는 1–{MAX_COLORS}")
    rgb = load_rgb(path, simplify)
    labels, background = quantize(rgb, colors, simplify, draw_background)
    found = []
    for label in np.unique(labels):
        if label == background:
            continue
        region = mask_region(labels == label)
        if not region.is_empty:
            found.append((tuple(int(v) for v in rgb[labels == label].mean(axis=0).round()), region))
    found = [item for item in found if _mean_width(item[1]) >= MIN_LAYER_WIDTH_PX]
    total = sum(region.area for _, region in found)
    found = sorted((item for item in found if item[1].area >= MIN_LAYER_RATIO * total),
                   key=lambda item: -item[1].area)
    if not found:
        raise ImageRegionError("그릴 윤곽이 없음: --colors, --draw-background 검토")
    xs0, ys0, xs1, ys1 = zip(*(region.bounds for _, region in found))
    x0, y0, x1, y1 = min(xs0), min(ys0), max(xs1), max(ys1)
    layers = [ColorLayer(color, affinity.translate(region, -(x0 + x1) / 2, -(y0 + y1) / 2)) for color, region in found]
    reach = max(float(np.hypot(*shapely.get_coordinates(layer.region).T).max()) for layer in layers)
    return ImageRegions(layers, max(x1 - x0, y1 - y0), reach)
