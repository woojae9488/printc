"""cream_image 단위 테스트. 임시 프로필과 Pillow로 만든 이미지, 가짜 추적기로 검증함."""

from __future__ import annotations

import io
import math
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PIL import Image, ImageDraw, ImageFilter

    import cream_image as ci
    import cream_text as ct
    from test_cream_text import PROFILE, TempProfileTest, extrude_runs, make_job
except ImportError as exc:
    raise unittest.SkipTest(f"이미지 의존 없음(tools/requirements.txt): {exc}")

BLACK, RED, BLUE, YELLOW = (0, 0, 0), (220, 40, 60), (30, 60, 200), (250, 200, 30)
Color = str | tuple[int, ...]


def draw_png(path: Path, shapes: list[tuple[tuple[int, int, int, int], Color]],
             size: tuple[int, int] = (200, 200)) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for box, color in shapes:
        draw.rectangle(box, fill=color)
    img.save(path)
    return path


@dataclass(frozen=True)
class FakeLayer:
    color: str

    @property
    def hex(self) -> str:
        return self.color


@dataclass(frozen=True)
class FakeRegion:
    """긴 변 side, 원점에서 가장 먼 점 reach인 가짜 영역. 색마다 조각 count개, 조각은 size를 그대로 넘김."""

    side: float = 100.0
    reach: float = 80.0
    count: int = 1
    layers: Sequence[ci.LayerLike] = field(default_factory=lambda: [FakeLayer("#aa0000")])
    photo: bool = False

    # noinspection PyUnusedLocal
    def parts(self, index: int, size: float) -> list[float]:
        return [size] * self.count


@dataclass(frozen=True)
class FakeTraced:
    strokes: list[ct.Stroke]
    advance: float = 0.0
    too_thin: bool = False
    method: str = "fill"
    min_char_height: float | None = None


def line_trace(part: float, *_: object) -> FakeTraced:
    """원점에서 +x로 part/2 만큼 가는 선 한 줄."""
    return FakeTraced([[(0.0, 0.0), (part / 2, 0.0)]])


def fallback_trace(*_: object) -> FakeTraced:
    return FakeTraced([[(0.0, 0.0), (5.0, 0.0)]], method="center")


def thin_trace(*_: object) -> FakeTraced:
    return FakeTraced([], too_thin=True)


TWO_COLORS = FakeRegion(layers=[FakeLayer("#aa0000"), FakeLayer("#0000bb")])


def make_layers(job: ct.Job, region: FakeRegion = FakeRegion(), trace: ci.TraceFn = line_trace,
                **opts: object) -> list[ci.Layer]:
    return ci.image_layers(job, region, ci.ImageOptions(Path("a.png"), **opts), trace)


class ImageLayersTest(unittest.TestCase):
    def test_auto_size_fits_limit(self) -> None:
        job = make_job(char_height=None)
        (layer,) = make_layers(job)
        self.assertTrue(layer.layout.auto_height)
        self.assertAlmostEqual(layer.layout.char_height, 100 * ct.text_limit_radius(job) / 80)

    def test_given_size(self) -> None:
        (layer,) = make_layers(make_job(char_height=60))
        self.assertFalse(layer.layout.auto_height)
        self.assertEqual(layer.layout.strokes, [[(0.0, 0.0), (30.0, 0.0)]])

    def test_layers_follow_source_order_with_one_size(self) -> None:
        first, second = make_layers(make_job(char_height=60), TWO_COLORS)
        self.assertEqual((first.color, second.color), ("#aa0000", "#0000bb"))
        self.assertEqual(first.layout.char_height, second.layout.char_height)
        self.assertEqual(first.layout.colors, ["#aa0000"])
        self.assertIn("#0000bb", second.layout.lines[0])

    def test_given_size_outside_cake_without_tracing(self) -> None:
        def fail(*_: object) -> FakeTraced:
            raise AssertionError("윤곽이 밖이면 획을 만들지 않아야 함")

        job = make_job(char_height=400)
        with self.assertRaisesRegex(ct.CreamTextError, "그림이 케이크 반경 밖"):
            make_layers(job, trace=fail)

    def test_given_size_strokes_outside_room(self) -> None:
        def long_trace(part: float, *_: object) -> FakeTraced:
            return FakeTraced([[(0.0, 0.0), (part * 1.5, 0.0)]])

        job = make_job(char_height=90)
        with self.assertRaisesRegex(ct.CreamTextError, "케이크 반경 밖"):
            make_layers(job, trace=long_trace)

    def test_auto_shrinks_when_strokes_exceed_room(self) -> None:
        def long_trace(part: float, *_: object) -> FakeTraced:
            return FakeTraced([[(0.0, 0.0), (part * 1.5, 0.0)]])

        job = make_job(char_height=None)
        (layer,) = make_layers(job, trace=long_trace)
        self.assertLessEqual(ct.max_radius(layer.layout.strokes), ct.text_room(job))

    def test_too_small(self) -> None:
        job = make_job(char_height=10)
        with self.assertRaisesRegex(ct.CreamTextError, "최소"):
            make_layers(job)

    def test_thin_parts_are_skipped(self) -> None:
        def mixed(*_: object) -> FakeTraced:
            thin = next(calls)
            return FakeTraced([] if thin else [[(0.0, 0.0), (5.0, 0.0)]], too_thin=thin)

        calls = iter([True, False])
        (layer,) = make_layers(make_job(char_height=60), FakeRegion(count=2), mixed)
        self.assertEqual(layer.layout.skipped, ["#1"])
        self.assertEqual(len(layer.layout.strokes), 1)

    def test_auto_fallback_uses_best_ladder(self) -> None:
        seen = []

        def spy(part: float, ref: float, lw: float, style: str, fb: str) -> FakeTraced:
            seen.append(fb)
            return line_trace(part, ref, lw, style, fb)

        make_layers(make_job(char_height=60), trace=spy)
        make_layers(make_job(char_height=60), trace=spy, fallback="center")
        self.assertEqual(seen, [ci.TRACE_FALLBACK, "center"])

    def test_layer_without_strokes_is_dropped(self) -> None:
        def trace(part: float, *args: object) -> FakeTraced:
            return thin_trace() if next(order) else line_trace(part, *args)

        order = iter([True, False])
        layers = make_layers(make_job(char_height=60), TWO_COLORS, trace)
        self.assertEqual([layer.color for layer in layers], ["#0000bb"])

    def test_all_skipped(self) -> None:
        job = make_job(char_height=60)
        with self.assertRaisesRegex(ct.CreamTextError, "짤 획이 없음"):
            make_layers(job, trace=thin_trace)

    def test_fallback_marked(self) -> None:
        (layer,) = make_layers(make_job(char_height=60), trace=fallback_trace)
        self.assertEqual(layer.layout.fallback, {0})
        self.assertEqual(layer.layout.fallback_counts, {"center": 1})

    def test_label_has_no_control_chars(self) -> None:
        self.assertEqual(ci._label(Path("/x/a\nG1 X0.png")), "a?G1 X0.png")


class CombineTest(unittest.TestCase):
    @staticmethod
    def layers() -> list[ci.Layer]:
        return make_layers(make_job(char_height=60), TWO_COLORS, fallback_trace)

    def test_per_tool(self) -> None:
        merged = ci.combine("a.png", self.layers(), per_tool=True)
        self.assertEqual(merged.tools, [0, 1])
        self.assertEqual(merged.colors, ["#aa0000", "#0000bb"])
        self.assertEqual(merged.fallback, {0, 1})
        self.assertEqual(merged.fallback_counts, {"center": 2})

    def test_shared_tool(self) -> None:
        self.assertEqual(ci.combine("a.png", self.layers(), per_tool=False).tools, [])

    def test_numbered(self) -> None:
        path = Path("out/job.gcode")
        self.assertEqual(ci.numbered(path, 1, 1), path)
        self.assertEqual(ci.numbered(path, 2, 3), Path("out/job_2.gcode"))


class CliTest(TempProfileTest):
    def run_image(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ci.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def size_of(message: str) -> float:
        return float(message.split("그림 크기 ")[1].split(" mm")[0])

    def image(self) -> Path:
        return draw_png(self.root / "pic.png", [((20, 20, 180, 180), BLACK)])

    def color_image(self) -> Path:
        return draw_png(self.root / "color.png", [((10, 10, 189, 189), RED), ((60, 60, 139, 139), YELLOW),
                                                  ((85, 85, 114, 114), BLUE)])

    def gcode(self, name: str) -> str:
        return (self.root / "out" / name).read_text()

    def test_one_color(self) -> None:
        code, out, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--colors", "1",
                                        "--preview", "out/pic.svg")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("자동", out)
        self.assertTrue((self.root / "out" / "pic.svg").exists())
        self.assertIn("; printc cream_image: pic.png", self.gcode("pic.gcode"))
        self.assertFalse((self.root / "out" / "pic_1.gcode").exists())
        radius = ct.text_limit_radius(make_job(char_height=None))
        for run in extrude_runs(self.gcode("pic.gcode")):
            for x, y in run:
                self.assertLessEqual(math.hypot(x - 160, y - 117.5), radius)

    def test_split_writes_file_per_color(self) -> None:
        code, out, err = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--preview",
                                        "out/c.svg")
        self.assertEqual((code, err), (0, ""))
        files = sorted(p.name for p in (self.root / "out").glob("c_*.gcode"))
        self.assertEqual(files, ["c_1.gcode", "c_2.gcode", "c_3.gcode"])
        self.assertEqual(len(re.findall(r"생성: out/c_\d\.gcode \(#[0-9a-f]{6}, ", out)), 3)
        for name in files:
            text = self.gcode(name)
            self.assertEqual(re.findall(r"^T\d$", text, re.M), ["T0"])
            self.assertRegex(text.splitlines()[0], r"^; printc cream_image: color\.png #[0-9a-f]{6}$")
        svg = (self.root / "out" / "c.svg").read_text()
        self.assertEqual(len(set(re.findall(r'<polyline stroke="(#[0-9a-f]{6})"', svg))), 3)

    def test_split_shares_position_and_size(self) -> None:
        self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode")
        xs = [x for n in ("c_1.gcode", "c_2.gcode", "c_3.gcode") for run in extrude_runs(self.gcode(n))
              for x, _ in run]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 160.0, delta=1.0)

    def test_single_switches_tools(self) -> None:
        code, out, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--files", "single")
        self.assertEqual(code, 0)
        self.assertEqual(re.findall(r"^T\d$", self.gcode("c.gcode"), re.M), ["T0", "T1", "T2"])
        self.assertRegex(out, r"\(T0 #[0-9a-f]{6}, T1 #[0-9a-f]{6}, T2 #[0-9a-f]{6}, 짜는 크림")
        header = self.gcode("c.gcode").splitlines()[0]
        self.assertRegex(header, r"^; printc cream_image: color\.png / T0 #[0-9a-f]{6} / T1 ")
        self.assertFalse((self.root / "out" / "c_1.gcode").exists())

    def test_single_needs_enough_cartridges(self) -> None:
        code, _, err = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--files", "single",
                                      "--colors", "4")
        self.assertEqual(code, 1)
        self.assertIn("--files split", err)

    def test_fewer_colors_than_requested(self) -> None:
        code, out, _ = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode")
        self.assertEqual(code, 0)
        self.assertIn("색 1개만 남음(요청 3개)", out)

    def test_border_and_flowers_need_one_color_and_fail_before_reading_the_image(self) -> None:
        missing = str(self.root / "none.png")  # 읽기 전에 막으면 읽기 오류가 아니라 이 오류가 나옴
        for flag in ("--border", "--flowers"):
            extra = [flag] if flag == "--border" else [flag, "6"]
            code, _, err = self.run_image(missing, *PROFILE, "--out", "out/c.gcode", *extra)
            self.assertEqual(code, 1)
            self.assertIn("--colors 1", err)
            self.assertNotIn("읽기 실패", err)
        code, _, _ = self.run_image(str(self.image()), *PROFILE, "--out", "out/a.gcode", "--colors", "1", "--border",
                                    "--flowers", "6")
        self.assertEqual(code, 0)
        self.assertEqual(re.findall(r"^T\d$", self.gcode("a.gcode"), re.M), ["T0", "T1", "T2"])

    def test_draw_background_needs_two_colors(self) -> None:
        code, _, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/a.gcode", "--colors", "1",
                                      "--draw-background")
        self.assertEqual(code, 1)
        self.assertIn("--colors 2 이상", err)

    def test_nothing_is_left_when_a_write_fails(self) -> None:
        real = Path.write_text
        calls = []

        def flaky(path: Path, *args: object, **kw: object) -> int:
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError("디스크 가득 참")
            return real(path, *args, **kw)

        (self.root / "out").mkdir()
        (self.root / "out" / "c_1.gcode").write_text("이전 실행")
        with mock.patch.object(Path, "write_text", flaky):
            code, _, err = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--preview",
                                          "out/c.svg")
        self.assertEqual((code, "디스크" in err), (1, True))
        # 기존 파일은 그대로이고 새 파일, 임시 파일은 없음.
        self.assertEqual(sorted(p.name for p in (self.root / "out").iterdir()), ["c_1.gcode"])
        self.assertEqual((self.root / "out" / "c_1.gcode").read_text(), "이전 실행")

    def test_stale_check_escapes_glob_characters(self) -> None:
        (self.root / "out").mkdir()
        (self.root / "out" / "cake1_9.gcode").write_text("다른 파일")
        (self.root / "out" / "cake[1]_9.gcode").write_text("이전 파일")
        _, out, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/cake[1].gcode")
        self.assertIn("cake[1]_9.gcode", out)
        self.assertNotIn("cake1_9.gcode", out)

    def test_border_and_flowers_shrink_image(self) -> None:
        plain = self.run_image(str(self.image()), *PROFILE, "--out", "out/a.gcode", "--colors", "1")[1]
        framed = self.run_image(str(self.image()), *PROFILE, "--out", "out/b.gcode", "--colors", "1", "--border",
                                "--flowers", "6")[1]
        self.assertLess(self.size_of(framed), self.size_of(plain))
        limit = ct.text_limit_radius(make_job(char_height=None, border=True, flowers=6))
        # 테두리(T1), 꽃(T2)은 반경 위에 있고 그림(T0)만 limit 안.
        others = (105 - 8, 105 - 20)
        for run in extrude_runs(self.gcode("b.gcode")):
            for x, y in run:
                radius = math.hypot(x - 160, y - 117.5)
                self.assertTrue(radius <= limit or any(math.isclose(radius, r, abs_tol=0.01) for r in others), radius)

    def test_stale_numbered_files_are_reported(self) -> None:
        self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--colors", "3")
        (self.root / "out" / "c_9.gcode").write_text("old")
        (self.root / "out" / "c_x.gcode").write_text("other")
        _, out, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--colors", "3")
        self.assertIn("c_9.gcode", out)
        self.assertNotIn("c_x.gcode", out)
        self.assertNotIn("c_1.gcode", out.split("경고")[-1])
        _, out, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--files", "single")
        self.assertIn("c_1.gcode", out)

    def test_numbered_path_must_stay_in_repo(self) -> None:
        outside = self.root.parent / "outside.gcode"
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        (self.root / "out").mkdir()
        (self.root / "out" / "c_2.gcode").symlink_to(outside)
        code, _, err = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode")
        self.assertEqual(code, 1)
        self.assertIn("저장소 밖", err)
        self.assertFalse(outside.exists())
        self.assertFalse((self.root / "out" / "c_1.gcode").exists())

    def test_nothing_written_when_a_build_fails(self) -> None:
        real = ct.build_gcode
        calls = []

        def flaky(*args: object) -> tuple[str, dict[int, float]]:
            calls.append(1)
            if len(calls) == 2:
                raise ct.CreamTextError("실패")
            return real(*args)

        with mock.patch.object(ct, "build_gcode", flaky):
            code, _, err = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode")
        self.assertEqual((code, "실패" in err), (1, True))
        self.assertEqual(list((self.root / "out").glob("c_*.gcode")), [])

    def test_antialiased_edges_do_not_become_colors(self) -> None:
        img = Image.new("RGB", (200, 200), "white")
        ImageDraw.Draw(img).rectangle((50, 50, 150, 150), fill="black")
        img.filter(ImageFilter.GaussianBlur(0.8)).save(self.root / "blur.png")
        for flag in ("--simplify", "--no-simplify"):
            _, out, _ = self.run_image(str(self.root / "blur.png"), *PROFILE, "--out", "out/blur.gcode", flag)
            self.assertEqual(out.count("생성:"), 1, flag)

    def test_simplify_flag(self) -> None:
        self.assertTrue(ci.parse_args([str(self.image()), "--out", "a.gcode"]).simplify)
        self.assertFalse(ci.parse_args([str(self.image()), "--out", "a.gcode", "--no-simplify"]).simplify)
        code, _, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/n.gcode", "--no-simplify")
        self.assertEqual(code, 0)

    def test_draw_background(self) -> None:
        code, out, _ = self.run_image(str(self.color_image()), *PROFILE, "--out", "out/c.gcode", "--colors", "4",
                                      "--draw-background")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("생성:"), 4)

    def test_given_size(self) -> None:
        code, out, _ = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--size", "80")
        self.assertEqual(code, 0)
        self.assertIn("그림 크기 80.0 mm(지정)", out)

    def test_size_not_finite_or_positive(self) -> None:
        for bad in ("nan", "inf", "-5", "0"):
            code, _, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", f"--size={bad}")
            self.assertEqual(code, 1, bad)
            self.assertIn("--size", err)

    def test_size_too_big(self) -> None:
        code, _, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--size", "300")
        self.assertEqual(code, 1)
        self.assertIn("케이크 반경 밖", err)

    def test_bad_colors(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ci.parse_args([str(self.image()), "--out", "a.gcode", "--colors", "0"])
        code, _, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--colors", "99")
        self.assertEqual(code, 1)
        self.assertIn("색 수", err)

    def test_missing_image(self) -> None:
        code, _, err = self.run_image(str(self.root / "none.png"), *PROFILE, "--out", "out/pic.gcode")
        self.assertEqual(code, 1)
        self.assertIn("이미지 읽기 실패", err)

    def test_thin_part_is_skipped_with_warning(self) -> None:
        thin = draw_png(self.root / "thin.png", [((20, 20, 180, 180), BLACK), ((0, 195, 199, 197), BLACK)])
        args = (str(thin), *PROFILE, "--out", "out/thin.gcode", "--colors", "1", "--fallback", "none", "--size", "120")
        code, _, err = self.run_image(*args)
        self.assertEqual(code, 0)
        self.assertIn("건너뜀", err)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ci.parse_args([*args, "--allow-thin"])

    def test_photo_flag(self) -> None:
        parse = lambda *flags: ci.parse_args([str(self.image()), "--out", "a.gcode", *flags]).photo  # noqa: E731
        self.assertEqual((parse(), parse("--photo"), parse("--no-photo")), (None, True, False))

    def test_photo_message(self) -> None:
        out = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--photo")[1]
        self.assertIn("사진 보정 적용", out)
        out = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode", "--no-photo")[1]
        self.assertNotIn("사진 보정", out)

    def test_missing_dependency_hint(self) -> None:
        with mock.patch.dict(sys.modules, {"imageregion": None}):
            code, _, err = self.run_image(str(self.image()), *PROFILE, "--out", "out/pic.gcode")
        self.assertEqual(code, 1)
        self.assertIn("pip install", err)


if __name__ == "__main__":
    unittest.main()
