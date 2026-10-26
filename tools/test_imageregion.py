"""imageregion 단위 테스트. Pillow로 만든 작은 이미지로 검증함."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import numpy as np
    from PIL import Image, ImageDraw

    import imageregion as ir
except ImportError as exc:
    raise unittest.SkipTest(f"이미지 의존 없음(tools/requirements.txt): {exc}")

RED, BLUE, YELLOW, BLACK = (220, 40, 60), (30, 60, 200), (250, 200, 30), (0, 0, 0)
Box = tuple[int, int, int, int]


def paint(path: Path, shapes: list[tuple[Box, object]], size: tuple[int, int] = (400, 400), mode: str = "RGB",
          bg: object = "white") -> Path:
    """shapes는 (상자, 색). 상자는 끝점 포함."""
    img = Image.new(mode, size, bg)
    draw = ImageDraw.Draw(img)
    for box, color in shapes:
        draw.rectangle(box, fill=color)
    img.save(path)
    return path


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def two_boxes(self) -> Path:
        # 빨강 200x200, 파랑 100x100. 서로 떨어져 있음.
        return paint(self.dir / "a.png", [((20, 20, 219, 219), RED), ((260, 260, 359, 359), BLUE)])


class MaskRegionTest(unittest.TestCase):
    def test_rectangle(self) -> None:
        mask = np.zeros((10, 20), dtype=bool)
        mask[2:8, 5:15] = True
        region = ir.mask_region(mask)
        self.assertAlmostEqual(region.area, 60.0)
        self.assertEqual(region.bounds, (5.0, 2.0, 15.0, 8.0))

    def test_y_is_up(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[0:2, :] = True  # 이미지 맨 위 두 행
        self.assertEqual(ir.mask_region(mask).bounds, (0.0, 8.0, 10.0, 10.0))

    def test_drops_specks_and_small_holes(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        mask[5:30, 5:30] = True
        mask[15:17, 15:17] = False  # 구멍 4px²
        mask[35, 35] = True  # 잡티
        region = ir.mask_region(mask)
        self.assertEqual(len(region.geoms), 1)
        self.assertEqual(len(region.geoms[0].interiors), 0)

    def test_keeps_big_hole(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        mask[5:35, 5:35] = True
        mask[15:25, 15:25] = False
        self.assertEqual(len(ir.mask_region(mask).geoms[0].interiors), 1)

    def test_checkerboard_at_work_size_hits_limit(self) -> None:
        mask = np.indices((ir.WORK_MAX_PX, ir.WORK_MAX_PX)).sum(axis=0) % 2 == 0
        with self.assertRaisesRegex(ir.ImageRegionError, "복잡"):
            ir.mask_region(mask)

    def test_too_many_runs(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[::2, ::2] = True
        with mock.patch.object(ir, "MAX_RUNS", 5):
            with self.assertRaisesRegex(ir.ImageRegionError, "복잡"):
                ir.mask_region(mask)


class QuantizeTest(unittest.TestCase):
    def image(self) -> np.ndarray:
        rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
        rgb[10:60, 10:60] = RED
        rgb[70:95, 70:95] = YELLOW
        return rgb

    def test_centers_are_deterministic_and_find_each_color(self) -> None:
        pixels = self.image().reshape(-1, 3)
        first = ir.kmeans_centers(pixels, 3)
        np.testing.assert_array_equal(first, ir.kmeans_centers(pixels, 3))
        for color in ((255, 255, 255), RED, YELLOW):
            self.assertTrue(np.linalg.norm(first - color, axis=1).min() < 5)

    def test_rare_blend_color_is_not_a_seed(self) -> None:
        rgb = self.image()
        rgb[0, :20] = (235, 120, 50)  # 가장자리가 섞인 드문 색
        centers = ir.kmeans_centers(rgb.reshape(-1, 3), 3)
        self.assertTrue(np.linalg.norm(centers - YELLOW, axis=1).min() < 5)

    def test_background_is_color_touching_the_border(self) -> None:
        labels, background = ir.quantize(self.image(), 2, simplify=False, draw_background=False)
        self.assertEqual(background, {int(labels[0, 0])})
        self.assertEqual(len(np.unique(labels)), 3)

    def test_draw_background_keeps_it(self) -> None:
        labels, background = ir.quantize(self.image(), 3, simplify=False, draw_background=True)
        self.assertEqual(background, set())
        self.assertEqual(len(np.unique(labels)), 3)

    def test_close_colors_merge(self) -> None:
        rgb = np.full((60, 60, 3), 255, dtype=np.uint8)
        rgb[10:50, 10:30] = (200, 40, 60)
        rgb[10:50, 30:50] = (205, 42, 58)  # 거의 같은 빨강
        for simplify in (False, True):  # 합친 뒤 빈 라벨이 남아도 다수결이 안전해야 함
            labels, _ = ir.quantize(rgb, 2, simplify=simplify, draw_background=False)
            self.assertEqual(len(np.unique(labels)), 2, simplify)

    def test_simplify_removes_isolated_pixels(self) -> None:
        rgb = self.image()
        rgb[30, 30] = (255, 255, 255)  # 빨강 안의 흰 점
        rgb[80, 5] = (255, 0, 0)  # 배경 위 빨강 점
        raw, _ = ir.quantize(rgb, 2, simplify=False, draw_background=False)
        smooth, _ = ir.quantize(rgb, 2, simplify=True, draw_background=False)
        self.assertNotEqual(raw[30, 30], raw[40, 40])
        self.assertEqual(smooth[30, 30], smooth[40, 40])
        self.assertEqual(smooth[80, 5], smooth[0, 0])


class LoadRegionsTest(TempDirTest):
    def test_layers_by_area_with_shared_center(self) -> None:
        regions = ir.load_regions(self.two_boxes(), 2)
        self.assertEqual(len(regions.layers), 2)
        red, blue = regions.layers
        self.assertLess(np.linalg.norm(np.array(red.color) - RED), 10)
        self.assertLess(np.linalg.norm(np.array(blue.color) - BLUE), 10)
        self.assertGreater(red.region.area, blue.region.area)
        xs0, ys0, xs1, ys1 = zip(*(layer.region.bounds for layer in regions.layers))
        self.assertAlmostEqual(min(xs0) + max(xs1), 0.0, places=3)
        self.assertAlmostEqual(min(ys0) + max(ys1), 0.0, places=3)
        self.assertAlmostEqual(regions.side, 340, delta=2)  # 20..359 → 340px
        self.assertGreater(regions.reach, regions.side / 2)

    def test_hex(self) -> None:
        layer = ir.load_regions(self.two_boxes(), 2).layers[0]
        self.assertRegex(layer.hex, r"^#[0-9a-f]{6}$")

    def test_fewer_layers_than_colors(self) -> None:
        self.assertEqual(len(ir.load_regions(self.two_boxes(), 6).layers), 2)

    def test_draw_background(self) -> None:
        layers = ir.load_regions(self.two_boxes(), 3, draw_background=True).layers
        self.assertEqual(len(layers), 3)

    def test_dark_background_is_skipped_by_border(self) -> None:
        path = paint(self.dir / "d.png", [((100, 100, 299, 299), (255, 255, 255))], bg="black")
        (layer,) = ir.load_regions(path, 1).layers
        self.assertGreater(min(layer.color), 200)

    def test_transparent_is_white(self) -> None:
        path = paint(self.dir / "t.png", [((100, 100, 299, 299), (0, 0, 0, 255))], mode="RGBA", bg=(0, 0, 0, 0))
        (layer,) = ir.load_regions(path, 1).layers
        self.assertLess(max(layer.color), 30)

    def test_tiny_color_is_dropped(self) -> None:
        path = paint(self.dir / "s.png", [((20, 20, 359, 359), RED), ((100, 100, 103, 103), YELLOW)])
        self.assertEqual(len(ir.load_regions(path, 3, simplify=False).layers), 1)

    def test_blank_image(self) -> None:
        path = self.dir / "blank.png"
        Image.new("RGB", (50, 50), "white").save(path)
        with self.assertRaisesRegex(ir.ImageRegionError, "윤곽이 없음"):
            ir.load_regions(path, 3)

    def test_color_count_range(self) -> None:
        for bad in (0, ir.MAX_COLORS + 1):
            with self.assertRaisesRegex(ir.ImageRegionError, "색 수"):
                ir.load_regions(self.two_boxes(), bad)

    def test_small_image_is_enlarged(self) -> None:
        path = paint(self.dir / "small.png", [((8, 4, 31, 19), BLACK)], size=(40, 24))
        self.assertGreater(ir.load_regions(path, 1).side, ir.WORK_MAX_PX / 2)

    def test_downscales_large_image(self) -> None:
        path = paint(self.dir / "big.png", [((200, 100, 1799, 899), BLACK)], size=(2000, 1000))
        self.assertLessEqual(ir.load_regions(path, 1).side, ir.WORK_MAX_PX)

    def test_palette_gif_first_frame(self) -> None:
        path = self.dir / "a.gif"
        frames = [Image.new("P", (100, 100), 0) for _ in range(2)]
        ImageDraw.Draw(frames[0]).rectangle((10, 10, 59, 59), fill=1)
        for frame in frames:
            frame.putpalette([255, 255, 255, 0, 0, 0])
        frames[0].save(path, save_all=True, append_images=frames[1:])
        self.assertGreater(ir.load_regions(path, 1).side, 0)

    def test_rejects_non_image(self) -> None:
        path = self.dir / "a.txt"
        path.write_text("not an image")
        with self.assertRaisesRegex(ir.ImageRegionError, "읽기 실패"):
            ir.load_regions(path, 1)
        with self.assertRaisesRegex(ir.ImageRegionError, "읽기 실패"):
            ir.load_regions(self.dir / "none.png", 1)

    def test_rejects_other_formats(self) -> None:
        path = self.dir / "a.tiff"
        Image.new("RGB", (20, 20), "black").save(path)
        with self.assertRaisesRegex(ir.ImageRegionError, "읽기 실패"):
            ir.load_regions(path, 1)

    def test_rejects_huge_source(self) -> None:
        with mock.patch.object(ir, "MAX_SOURCE_PIXELS", 100):
            with self.assertRaisesRegex(ir.ImageRegionError, "너무 큼"):
                ir.load_regions(self.two_boxes(), 2)


def shaded(img: Image.Image, top: float = 0.55, bottom: float = 1.0) -> Image.Image:
    """위가 어둡고 아래가 밝은 조명을 곱함."""
    arr = np.asarray(img, dtype=np.float64)
    shade = np.linspace(top, bottom, arr.shape[0])[:, None, None]
    return Image.fromarray(np.clip(arr * shade, 0, 255).astype(np.uint8))


class PhotoTest(TempDirTest):
    """천 위에 놓은 흰 스티커를 조명이 고르지 않게 찍은 사진."""

    CLOTH, PINK, BLUE, DARK = (150, 200, 190), (230, 130, 170), (80, 140, 220), (60, 40, 60)
    # 조명을 편 뒤 스티커 바탕의 최소 채널값은 약 190, 그림 색은 100 이하. 그 사이.
    BASE_MIN = 150

    def sticker(self, name: str = "photo.png", shadow: bool = True) -> Path:
        img = Image.new("RGB", (600, 800), self.CLOTH)
        draw = ImageDraw.Draw(img)
        draw.ellipse((100, 150, 500, 550), fill=(250, 248, 246))
        draw.ellipse((140, 190, 460, 510), outline=self.PINK, width=30)
        draw.rectangle((250, 280, 350, 400), fill=self.BLUE)
        draw.rectangle((235, 270, 365, 285), fill=self.DARK)
        draw.line((300, 0, 300, 800), fill=(200, 235, 225), width=3)  # 접힌 선
        if shadow:
            draw.polygon([(0, 640), (350, 640), (200, 800), (0, 800)], fill=(100, 140, 130))
        path = self.dir / name
        shaded(img).save(path)
        return path

    def test_auto_detects_photo_and_crops_to_subject(self) -> None:
        regions = ir.load_regions(self.sticker(), 3)
        self.assertTrue(regions.photo)
        # 대상(스티커) 크기에 맞춰 잘려 있어야 함. 그림자까지 포함하면 이보다 커짐.
        self.assertGreater(regions.side, 0.8 * ir.WORK_MAX_PX)
        self.assertLess(regions.side, ir.WORK_MAX_PX)
        self.assertLess(regions.reach, regions.side * 0.72)

    def test_cloth_shadow_and_sticker_base_are_not_colors(self) -> None:
        layers = ir.load_regions(self.sticker(), 3).layers
        self.assertEqual(len(layers), 3)
        for layer in layers:
            self.assertLess(min(layer.color), self.BASE_MIN, layer.hex)  # 스티커 흰 바탕이 아님
            self.assertGreater(np.linalg.norm(np.array(layer.color) - self.CLOTH), 40, layer.hex)  # 천, 그림자가 아님

    def test_draw_background_draws_only_the_sticker_base(self) -> None:
        layers = ir.load_regions(self.sticker(), 3, draw_background=True).layers
        self.assertEqual(len(layers), 3)
        self.assertEqual(sum(min(layer.color) > self.BASE_MIN for layer in layers), 1)

    def test_photo_can_be_forced_off(self) -> None:
        off = ir.load_regions(self.sticker(), 3, photo=False)
        self.assertFalse(off.photo)
        # 자르지 않으면 대상이 작업 해상도의 절반 정도로만 쓰임. 자르고 다시 줄이면 해상도가 늘어남.
        self.assertGreater(ir.load_regions(self.sticker(), 3).side, 1.5 * off.side)

    def test_gate_rejects_uneven_border(self) -> None:
        img = Image.new("RGB", (300, 300), (30, 160, 60))
        ImageDraw.Draw(img).rectangle((150, 0, 299, 299), fill=(240, 120, 30))  # 가장자리까지 찬 그림
        ImageDraw.Draw(img).ellipse((100, 100, 200, 200), fill=(20, 20, 20))
        path = self.dir / "bleed.png"
        img.save(path)
        self.assertFalse(ir.load_regions(path, 2).photo)

    def test_flatten_evens_light(self) -> None:
        arr = np.asarray(shaded(Image.new("RGB", (200, 200), (120, 160, 150)), 0.5, 1.0))
        coefs, reference = ir.fit_light(arr)
        flat = ir.flatten(arr, coefs, reference)
        self.assertLess(flat.reshape(-1, 3).std(axis=0).max(), 3)
        self.assertLess(ir.border_noise(flat), ir.border_noise(arr))

    def test_flatten_keeps_dark_background_dark(self) -> None:
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        arr[40:60, 40:60] = 255
        coefs, reference = ir.fit_light(arr)
        flat = ir.flatten(arr, coefs, reference)
        self.assertLess(int(flat[0, 0].max()), 5)
        self.assertGreater(int(flat[50, 50].min()), 250)

    def test_subject_shell(self) -> None:
        arr = np.full((200, 300, 3), (150, 200, 190), dtype=np.uint8)
        arr[60:140, 100:200] = (250, 240, 240)
        x0, y0, x1, y1 = ir.subject_shell(arr).bounds
        self.assertLess(x0, 100)
        self.assertGreater(x0, 100 - 0.05 * 100 - 6)
        self.assertGreater(x1, 200)
        self.assertLess(y1 - y0, 80 + 2 * 0.05 * 100)

    def test_subject_shell_none_when_subject_fills_image(self) -> None:
        arr = np.full((200, 300, 3), (150, 200, 190), dtype=np.uint8)
        arr[5:195, 5:295] = (250, 240, 240)
        self.assertIsNone(ir.subject_shell(arr))
        self.assertIsNone(ir.subject_shell(np.full((50, 50, 3), 128, dtype=np.uint8)))

    def test_background_labels_take_every_color_on_the_edge(self) -> None:
        labels = np.full((60, 60), 0)
        labels[:, 30:] = 1  # 가장자리 띠의 반쯤을 차지하는 스티커 바탕
        labels[20:40, 20:40] = 2
        valid = np.zeros((60, 60), dtype=bool)
        valid[5:55, 5:55] = True
        labels = np.where(valid, labels, -1)
        self.assertEqual(ir.background_labels(labels, valid, photo=True), {0, 1})
        self.assertEqual(ir.background_labels(labels, valid, photo=False), {0})


class PlainImageTest(TempDirTest):
    """사진이 아닌 일반 이미지는 자동 판정이 켜져도 사진 보정 없이 이전처럼 나와야 함."""

    def icon(self, name: str, size: int = 300, shapes: list | None = None, bg: object = "white") -> Path:
        img = Image.new("RGB", (size, size), bg)
        draw = ImageDraw.Draw(img)
        for kind, box, color in shapes or []:
            getattr(draw, kind)(box, fill=color)
        path = self.dir / name
        img.save(path)
        return path

    def test_subject_touching_the_border_is_kept(self) -> None:
        path = self.icon("a.png", shapes=[("ellipse", (0, 0, 299, 299), RED)])
        regions = ir.load_regions(path, 3)
        self.assertEqual([layer.color for layer in regions.layers], [regions.layers[0].color])
        self.assertFalse(regions.photo)

    def test_badge_keeps_both_colors(self) -> None:
        path = self.icon("b.png", shapes=[("ellipse", (0, 0, 299, 299), RED), ("ellipse", (90, 90, 210, 210), BLUE)])
        colors = {layer.color for layer in ir.load_regions(path, 3).layers}
        self.assertEqual(len(colors), 2)

    def test_tiny_original_keeps_subject(self) -> None:
        path = self.icon("c.png", size=8, shapes=[("rectangle", (2, 2, 5, 5), BLACK)])
        self.assertEqual(len(ir.load_regions(path, 1).layers), 1)

    def test_merge_uses_plain_lab_distance(self) -> None:
        """a, b 가중이 합치기 기준을 줄이면 채도 높은 색의 작은 편차가 따로 남음."""
        weights = np.array(ir.LAB_WEIGHTS) * ir.LAB_SCALE
        labels = np.array([[0, 1, 2]])
        centers = np.array([[50.0, 70.0, 50.0], [50.0, 78.0, 50.0], [20.0, 0.0, 0.0]]) * weights  # ΔE 8, 그리고 먼 색
        merged, _ = ir._merge_close(labels, centers)
        self.assertEqual(merged[0, 0], merged[0, 1])
        self.assertNotEqual(merged[0, 0], merged[0, 2])

    def test_one_color_draws_the_whole_silhouette(self) -> None:
        shapes = [("ellipse", (20, 20, 250, 250), RED), ("rectangle", (240, 240, 280, 280), BLUE)]
        path = self.icon("e.png", shapes=shapes)
        (layer,) = ir.load_regions(path, 1).layers
        both = ir.load_regions(path, 2).layers
        self.assertGreaterEqual(layer.region.area, 0.97 * sum(l.region.area for l in both))


class PartsTest(TempDirTest):
    def test_all_layers_share_one_scale(self) -> None:
        regions = ir.load_regions(self.two_boxes(), 2)
        parts = [p for i in range(2) for p in regions.parts(i, 120.0)]
        x0, y0, x1, y1 = (f(v) for f, v in zip((min, min, max, max), zip(*(p.bounds for p in parts))))
        self.assertAlmostEqual(max(x1 - x0, y1 - y0), 120.0)
        self.assertAlmostEqual(x0 + x1, 0.0, places=3)

    def test_reading_order(self) -> None:
        path = paint(self.dir / "o.png", [((240, 240, 369, 369), BLACK), ((20, 240, 149, 369), BLACK),
                                          ((20, 20, 149, 149), BLACK)])
        parts = ir.load_regions(path, 1).parts(0, 100.0)
        self.assertEqual([(p.centroid.x < 0, p.centroid.y > 0) for p in parts],
                         [(True, True), (True, False), (False, False)])


if __name__ == "__main__":
    unittest.main()
