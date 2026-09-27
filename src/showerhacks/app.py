"""Pixel-scaled Arcade dashboard for the latest TVOC and 50-reading trend."""

from .sensor import MAX_GAME_TVOC_PPB, DemoSensor, PicoSensor, Reading, SensorPoller
from .score_upload import upload_scores
from PIL import Image, ImageDraw, ImageFont
from arcade.types import LBWH
from arcade.gl import BufferDescription
import arcade
from time import monotonic
from pathlib import Path
from math import ceil
from datetime import datetime
from array import array
from functools import lru_cache
from concurrent.futures import Future, ThreadPoolExecutor
import string
import csv
import argparse
import os
import sys
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
CANVAS_WIDTH = 480
CANVAS_HEIGHT = 270
BACKGROUND = (8, 9, 28)
PANEL = (24, 31, 72)
PANEL_LIGHT = (39, 53, 113)
WHITE = (255, 248, 225)
MUTED = (171, 186, 223)
BLUE = (36, 172, 255)
PURPLE = (135, 75, 223)
ORANGE = (255, 105, 35)
GOLD = (255, 211, 54)
SILVER = (199, 207, 221)
BRONZE = (205, 123, 73)
RED = (242, 49, 62)
DEFAULT_THRESHOLD_PPB = 10
SCORE_CEILING_PPB = MAX_GAME_TVOC_PPB
SCORE_HALF_POINT_PPB = 75
METER_RISE_SECONDS = 5.0
SCORE_WIGGLE_OFFSETS = (0, 1, 2, 3, 2, 0, -2, -3, -1)
ROUND_SECONDS = 20.0
READY_SECONDS = 5.0
STRONG_SNIFF_SECONDS = 15.0
MAX_SNIFF_SECONDS = 5.0
SMALL_SNIFF_FRAMES = (0, 1, 2, 1)
STRONG_SNIFF_FRAMES = (2, 3, 2, 1)
MAX_SNIFF_FRAMES = (4, 5, 6, 5)
UNDERARM_FRAME_SEQUENCE = (0, 1, 2, 3, 2, 1)
NOSE_PATH = Path(__file__).resolve().parent / "assets" / "nose.png"
UNDERARM_PATH = Path(__file__).resolve().parent / "assets" / "underarm_2.png"
SHOWER_PATH = Path(__file__).resolve().parent / "assets" / "shower.png"
LEADERBOARD_QR_PATH = Path(__file__).resolve().parent / \
    "assets" / "leaderboard-qr.png"
FONT_PATH = Path(__file__).resolve().parent / "assets" / "hes-on-fire.ttf"
SCORE_FIELDS = (
    "initials", "started_at", "ended_at", "room_reference_ppb", "threshold_ppb",
    "score_lower_bound_ppb", "peak_tvoc_ppb", "score",
)


@lru_cache(maxsize=1024)
def bitmap_text(value: str, color: tuple[int, int, int], size: int,
                gradient_to: tuple[int, int, int] | None = None) -> arcade.Texture:
    """Rasterize UI text with hard pixels before enlarging the canvas."""
    arcade_size = size * 1.7 if size >= 9 else size
    font_size = round(arcade_size * 4 / 3)
    if size >= 9:
        font = ImageFont.truetype(FONT_PATH, font_size)
    else:
        try:
            font = ImageFont.truetype("LiberationSans-Regular.ttf", font_size)
        except OSError:
            font = ImageFont.load_default(size=font_size)
    left, top, right, bottom = font.getbbox(value)
    letter_spacing = 1 if 9 <= size <= 16 else 0
    mask = Image.new("L", (max(1, right - left + letter_spacing * max(0, len(value) - 1)),
                           max(1, bottom - top)))
    draw = ImageDraw.Draw(mask)
    draw.fontmode = "1"
    if letter_spacing:
        for index, char in enumerate(value):
            x = round(font.getlength(value[:index])) + index * letter_spacing
            draw.text((x - left, -top), char, font=font, fill=255)
    else:
        draw.text((-left, -top), value, font=font, fill=255)
    image = Image.new("RGBA", mask.size, (*color, 0))
    if gradient_to is not None:
        pixels = image.load()
        for y in range(image.height):
            blend = y / max(1, image.height - 1)
            row_color = tuple(round(start * (1 - blend) + end * blend)
                              for start, end in zip(color, gradient_to))
            for x in range(image.width):
                pixels[x, y] = (*row_color, 0)
    image.putalpha(mask)
    return arcade.Texture(image, hash=f"showerhacks-text:{size}:{color}:{gradient_to}:{value}")


def score_for(peak_tvoc: int, lower_bound_ppb: int) -> int:
    """Map the TVOC increase above the start line to a comparable 1–99 score."""
    increase = max(0, peak_tvoc - lower_bound_ppb)
    ceiling_fraction = SCORE_CEILING_PPB / (SCORE_CEILING_PPB + SCORE_HALF_POINT_PPB)
    fraction = increase / (increase + SCORE_HALF_POINT_PPB)
    return min(99, 1 + round(99 * fraction / ceiling_fraction))


def meter_segments(score: int) -> int:
    """Fill the 20-segment bar in proportion to the displayed 1–99 score."""
    if score <= 1:
        return 0
    return min(20, max(1, round((score - 1) / 98 * 20)))


def displayed_meter_segments(score: int, elapsed_seconds: float) -> int:
    """Reveal the score bar over the first five seconds of a round."""
    progress = min(1.0, max(0.0, elapsed_seconds / METER_RISE_SECONDS))
    return int(meter_segments(score) * progress)


def displayed_score(score: int, elapsed_seconds: float) -> int:
    """Add a small visual-only wobble to the large score number."""
    offset = SCORE_WIGGLE_OFFSETS[int(max(0.0, elapsed_seconds) * 3) % len(SCORE_WIGGLE_OFFSETS)]
    return max(1, min(99, score + offset))


def projected_rank(score: int, scores: list[dict[str, str]]) -> int:
    """Place this round after existing scores with the same value."""
    return 1 + sum(int(row["score"]) >= score for row in scores)


def top_score_record(scores: list[dict[str, str]]) -> tuple[int, str]:
    """Return the highest score and its initials, keeping the first tie."""
    if not scores:
        return 0, "---"
    row = max(scores, key=lambda item: int(item["score"]))
    return int(row["score"]), (row.get("initials") or "---")[:3].upper()


def nose_frame_index(remaining_seconds: float) -> int:
    if remaining_seconds > STRONG_SNIFF_SECONDS:
        elapsed = ROUND_SECONDS - remaining_seconds
        return SMALL_SNIFF_FRAMES[int(elapsed * 5) % len(SMALL_SNIFF_FRAMES)]
    if remaining_seconds > MAX_SNIFF_SECONDS:
        elapsed = STRONG_SNIFF_SECONDS - remaining_seconds
        return STRONG_SNIFF_FRAMES[int(elapsed * 6) % len(STRONG_SNIFF_FRAMES)]
    elapsed = MAX_SNIFF_SECONDS - remaining_seconds
    return MAX_SNIFF_FRAMES[int(elapsed * 8) % len(MAX_SNIFF_FRAMES)]


def load_scores(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as file:
        rows = [row for row in csv.DictReader(file)]
    for row in rows:
        row["score"] = str(score_for(
            int(row["peak_tvoc_ppb"]), int(row["score_lower_bound_ppb"])))
    return rows


def save_scores(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(
                field, "---" if field == "initials" else "") for field in SCORE_FIELDS})


def report_upload_result(future: Future[None]) -> None:
    if error := future.exception():
        print(f"S3 score upload failed: {error}", file=sys.stderr)


def _runs(active: list[bool]) -> list[tuple[int, int]]:
    result = []
    start = None
    for index, present in enumerate(active + [False]):
        if present and start is None:
            start = index
        elif not present and start is not None:
            result.append((start, index))
            start = None
    return result


def load_nose_frames() -> list[arcade.Texture]:
    image = Image.open(NOSE_PATH).convert("RGBA")
    alpha = image.getchannel("A")
    columns = _runs([alpha.crop((x, 0, x + 1, image.height)).getbbox()
                     is not None for x in range(image.width)])
    if len(columns) != 8:
        raise ValueError(
            f"Expected 8 frames in {NOSE_PATH}, found {len(columns)}")
    return [arcade.Texture(image.crop((left, 0, right, image.height)),
                           hash=f"showerhacks-nose-{index}")
            for index, (left, right) in enumerate(columns)]


def load_underarm_frames() -> list[arcade.Texture]:
    image = Image.open(UNDERARM_PATH).convert("RGBA")
    alpha = image.getchannel("A")
    columns = _runs([alpha.crop((x, 0, x + 1, image.height)).getbbox()
                     is not None for x in range(image.width)])
    if len(columns) != 4:
        raise ValueError(
            f"Expected 4 frames in {UNDERARM_PATH}, found {len(columns)}")
    return [arcade.Texture(image.crop((left, 0, right, image.height)),
                           hash=f"showerhacks-underarm-{index}")
            for index, (left, right) in enumerate(columns)]


def load_shower_frames() -> list[arcade.Texture]:
    image = Image.open(SHOWER_PATH).convert("RGBA")
    alpha = image.getchannel("A")
    columns = _runs([alpha.crop((x, 0, x + 1, image.height)).getbbox()
                     is not None for x in range(image.width)])
    columns = [(left, right) for left, right in columns if right - left > 8]
    if len(columns) != 2:
        raise ValueError(f"Expected 2 frames in {SHOWER_PATH}, found {len(columns)}")
    width = max(right - left for left, right in columns)
    frames = []
    for index, (left, right) in enumerate(columns):
        crop = image.crop((left, 0, right, image.height))
        frame = Image.new("RGBA", (width, image.height))
        frame.paste(crop, ((width - crop.width) // 2, 0))
        frames.append(arcade.Texture(frame, hash=f"showerhacks-shower-{index}"))
    return frames


def letter_keys():
    for row, letters in enumerate(("ABCDEFG", "HIJKLMN", "OPQRST", "UVWXYZ")):
        start_x = 252 if len(letters) == 7 else 267
        bottom = 111 - row * 31
        for column, letter in enumerate(letters):
            left = start_x + column * 30
            yield letter, left, bottom, left + 27, bottom + 27


class PixelCanvas:
    """Render at 480x270, then blit to an integer-scaled screen rectangle."""

    def __init__(self, window: arcade.Window) -> None:
        self.ctx = window.ctx
        self.texture = self.ctx.texture(
            (CANVAS_WIDTH, CANVAS_HEIGHT), components=4)
        self.texture.filter = self.ctx.NEAREST, self.ctx.NEAREST
        self.framebuffer = self.ctx.framebuffer(
            color_attachments=[self.texture])
        self.camera = arcade.Camera2D(render_target=self.framebuffer)
        self.buffer = self.ctx.buffer(reserve=4 * 5 * 4)
        self.geometry = self.ctx.geometry(
            content=[BufferDescription(
                self.buffer, "3f 2f", ["in_pos", "in_uv"])],
            mode=self.ctx.TRIANGLE_STRIP,
        )
        self.program = self.ctx.load_program(
            vertex_shader=":system:shaders/gui/surface_vs.glsl",
            fragment_shader=":system:shaders/gui/surface_fs.glsl",
        )
        self.resize(window.width, window.height)

    def resize(self, width: int, height: int) -> None:
        self.screen_width = width
        self.screen_height = height
        self.scale = max(
            1, min(width // CANVAS_WIDTH, height // CANVAS_HEIGHT))
        canvas_width = CANVAS_WIDTH * self.scale
        canvas_height = CANVAS_HEIGHT * self.scale
        left = (width - canvas_width) // 2
        bottom = (height - canvas_height) // 2
        right, top = left + canvas_width, bottom + canvas_height
        self.buffer.write(array("f", (
            left, bottom, 0, 0, 0,
            right, bottom, 0, 1, 0,
            left, top, 0, 0, 1,
            right, top, 0, 1, 1,
        )))

    def screen_to_canvas(self, x: float, y: float) -> tuple[float, float] | None:
        left = (self.screen_width - CANVAS_WIDTH * self.scale) // 2
        bottom = (self.screen_height - CANVAS_HEIGHT * self.scale) // 2
        virtual_x = (x - left) / self.scale
        virtual_y = (y - bottom) / self.scale
        if 0 <= virtual_x < CANVAS_WIDTH and 0 <= virtual_y < CANVAS_HEIGHT:
            return virtual_x, virtual_y
        return None

    def draw(self) -> None:
        self.texture.use(0)
        self.geometry.render(self.program)


class TVOCWindow(arcade.Window):
    def __init__(
        self,
        poller: SensorPoller,
        fullscreen: bool = True,
        window_scale: int = 1,
        threshold_ppb: int = DEFAULT_THRESHOLD_PPB,
        scores_file: Path = Path("showerhacks_scores.csv"),
        s3_bucket: str | None = None,
    ) -> None:
        width = TARGET_WIDTH if fullscreen else CANVAS_WIDTH * window_scale
        height = TARGET_HEIGHT if fullscreen else CANVAS_HEIGHT * window_scale
        super().__init__(width, height, "S H O W E R M A S T E R",
                         fullscreen=fullscreen, draw_rate=0.1)
        self.background_color = (0, 0, 0)
        self.poller = poller
        self.canvas = PixelCanvas(self)
        self.threshold_ppb = threshold_ppb
        self.scores_file = scores_file
        self.s3_bucket = s3_bucket
        self.score_upload_executor = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="score-upload")
                                      if s3_bucket else None)
        self.scores = load_scores(scores_file)
        self.best_score, self.best_initials = top_score_record(self.scores)
        self.nose_frames = load_nose_frames()
        self.underarm_frames = load_underarm_frames()
        self.shower_frames = load_shower_frames()
        self.leaderboard_qr = arcade.Texture(
            Image.open(LEADERBOARD_QR_PATH).convert("RGBA"),
            hash="showerhacks-leaderboard-qr")
        self.view_state = "start"
        self.peak_tvoc = 0
        self.room_reference_ppb = 0
        self.score_lower_bound_ppb = 0
        self.session_start: datetime | None = None
        self.session_end: datetime | None = None
        self.ready_started_monotonic: float | None = None
        self.round_started_monotonic: float | None = None
        self.initials = ""

    def on_resize(self, width: int, height: int) -> None:
        super().on_resize(width, height)
        if hasattr(self, "canvas"):
            self.canvas.resize(width, height)

    def _text(self, value: str, x: int, y: int, color: tuple[int, int, int], size: int,
              gradient_to: tuple[int, int, int] | None = None) -> None:
        texture = bitmap_text(value, color, size, gradient_to)
        arcade.draw_texture_rect(texture, LBWH(
            x, y, texture.width, texture.height), pixelated=True)

    def _line(self, x1: float, y1: float, x2: float, y2: float, color: tuple[int, int, int], width: int = 1) -> None:
        arcade.draw_line(x1, y1, x2, y2, color, width)

    def _chrome(self) -> None:
        """A few hard-edged, low resolution arcade scoreboard details."""
        arcade.draw_lrbt_rectangle_filled(0, 480, 263, 270, BLUE)
        arcade.draw_lrbt_rectangle_filled(0, 480, 0, 6, ORANGE)
        arcade.draw_lrbt_rectangle_filled(0, 480, 257, 260, PURPLE)
        stripe_offset = int(monotonic() * 24) % 48
        for x in range(-80, 560, 48):
            self._line(x - stripe_offset, 6, x + 32 -
                       stripe_offset, 20, PANEL_LIGHT, 3)
            self._line(x + stripe_offset, 247, x + 32 +
                       stripe_offset, 260, PANEL_LIGHT, 3)
        arcade.draw_lrbt_rectangle_filled(7, 11, 25, 245, BLUE)
        arcade.draw_lrbt_rectangle_filled(469, 473, 25, 245, ORANGE)

    def _button(self, left: int, right: int, bottom: int, top: int,
                fill: tuple[int, int, int], edge: tuple[int, int, int]) -> None:
        arcade.draw_lrbt_rectangle_filled(
            left + 3, right + 3, bottom - 3, top - 3, edge)
        arcade.draw_lrbt_rectangle_filled(left, right, bottom, top, fill)
        arcade.draw_lrbt_rectangle_filled(left, right, top - 4, top, edge)

    def on_mouse_press(self, x: float, y: float, button: int, modifiers: int) -> None:
        self.set_mouse_visible(False)
        point = self.canvas.screen_to_canvas(x, y)
        if point is None:
            return
        px, py = point
        if self.view_state == "start":
            if 80 <= px < 400 and 75 <= py < 150:
                room_reference = self.poller.room_reference()
                if room_reference is None or not self.poller.snapshot()[0]:
                    return
                self.poller.set_room_sampling(False)
                self.room_reference_ppb = room_reference
                self.score_lower_bound_ppb = room_reference + self.threshold_ppb
                self.session_start = None
                self.session_end = None
                self.peak_tvoc = 0
                self.round_started_monotonic = None
                self.ready_started_monotonic = monotonic()
                self.view_state = "ready"
            elif 140 <= px < 340 and 8 <= py < 45:
                self.view_state = "leaderboard"
        elif self.view_state == "initials":
            if 418 <= px < 463 and 190 <= py < 227:
                self.initials = self.initials[:-1]
            elif 252 <= px < 463 and 149 <= py < 182:
                if len(self.initials) == 3:
                    self.save_round()
            elif len(self.initials) < 3:
                for letter, left, bottom, right, top in letter_keys():
                    if left <= px < right and bottom <= py < top:
                        self.initials += letter
                        break
        elif self.view_state == "leaderboard" and 120 <= px < 360 and 8 <= py < 45:
            self.poller.set_room_sampling(True)
            self.view_state = "start"

    def remaining_seconds(self) -> float:
        if self.round_started_monotonic is None:
            return ROUND_SECONDS
        return max(0.0, ROUND_SECONDS - (monotonic() - self.round_started_monotonic))

    def ready_remaining_seconds(self) -> float:
        if self.ready_started_monotonic is None:
            return READY_SECONDS
        return max(0.0, READY_SECONDS - (monotonic() - self.ready_started_monotonic))

    def countdown_display(self) -> str:
        return f"{ceil(self.remaining_seconds() * 10) / 10:.1f}"

    def on_update(self, delta_time: float) -> None:
        if self.view_state == "ready" and self.ready_remaining_seconds() <= 0:
            self.session_start = datetime.now().astimezone()
            self.round_started_monotonic = monotonic()
            self.view_state = "meter"
        elif self.view_state == "meter" and self.remaining_seconds() <= 0:
            self.finish_round()

    def _update_peak(self, readings: list) -> None:
        if self.view_state == "meter" and self.session_start is not None:
            self.peak_tvoc = max(
                [self.peak_tvoc] +
                [item.tvoc_ppb for item in readings if item.taken_at >= self.session_start]
            )

    def finish_round(self) -> None:
        if self.view_state != "meter" or self.session_start is None:
            return
        readings, _ = self.poller.snapshot()
        self._update_peak(readings)
        self.session_end = datetime.now().astimezone()
        self.initials = ""
        self.view_state = "initials"

    def save_round(self) -> None:
        if self.view_state != "initials" or len(self.initials) != 3 or self.session_start is None or self.session_end is None:
            return
        score = score_for(self.peak_tvoc, self.score_lower_bound_ppb)
        if score > self.best_score:
            self.best_score = score
            self.best_initials = self.initials
        self.scores.append({
            "initials": self.initials,
            "started_at": self.session_start.isoformat(),
            "ended_at": self.session_end.isoformat(),
            "room_reference_ppb": str(self.room_reference_ppb),
            "threshold_ppb": str(self.threshold_ppb),
            "score_lower_bound_ppb": str(self.score_lower_bound_ppb),
            "peak_tvoc_ppb": str(self.peak_tvoc),
            "score": str(score),
        })
        save_scores(self.scores_file, self.scores)
        if self.score_upload_executor is not None and self.s3_bucket is not None:
            future = self.score_upload_executor.submit(
                upload_scores, self.scores_file, self.s3_bucket)
            future.add_done_callback(report_upload_result)
        self.view_state = "leaderboard"

    def _draw_trend(self, readings: list, bottom: int, top: int) -> None:
        left, right = 21, 459
        self._line(left, bottom, right, bottom, PANEL_LIGHT)
        self._line(left, top, right, top, PANEL_LIGHT)
        if not readings:
            return
        ceiling = max(100, max(item.tvoc_ppb for item in readings))
        points = [
            (left + index * (right - left) / 49, bottom +
             item.tvoc_ppb * (top - bottom) / ceiling)
            for index, item in enumerate(readings)
        ]
        for start, end in zip(points, points[1:]):
            self._line(*start, *end, ORANGE, 2)
        arcade.draw_circle_filled(*points[-1], 3, GOLD)
        self._text(f"0-{ceiling} PPB", 389, bottom - 17, MUTED, 7)

    def _draw_letter(self, letter: str, center_x: float, bottom: float, height: float) -> None:
        texture = bitmap_text(letter, GOLD, 24 if height >= 29 else 15, ORANGE)
        arcade.draw_texture_rect(texture, LBWH(
            round(center_x - texture.width / 2),
            round(bottom + (height - texture.height) / 2),
            texture.width, texture.height), pixelated=True)

    def _draw_nose(self) -> None:
        texture = self.nose_frames[nose_frame_index(self.remaining_seconds())]
        height = 142
        width = height * texture.width / texture.height
        arcade.draw_texture_rect(texture, LBWH(
            95 - width / 2, 87, width, height), pixelated=True)

    def _draw_underarm(self) -> None:
        elapsed = 0 if self.ready_started_monotonic is None else monotonic() - \
            self.ready_started_monotonic
        frame = UNDERARM_FRAME_SEQUENCE[int(
            elapsed * 6) % len(UNDERARM_FRAME_SEQUENCE)]
        texture = self.underarm_frames[frame]
        height = 225
        width = height * texture.width / texture.height
        arcade.draw_texture_rect(texture, LBWH(
            125 - width / 2, 20, width, height), pixelated=True)

    def _draw_shower(self, center_x: int, bottom: int, height: int, alpha: int) -> None:
        texture = self.shower_frames[int(monotonic() * 4) % len(self.shower_frames)]
        width = height * texture.width / texture.height
        arcade.draw_texture_rect(texture, LBWH(
            center_x - width / 2, bottom, width, height),
            alpha=alpha, pixelated=True)

    def _draw_initials(self) -> None:
        score = score_for(self.peak_tvoc, self.score_lower_bound_ppb)
        rank = projected_rank(score, self.scores)
        arcade.draw_lrbt_rectangle_filled(20, 231, 26, 235, PANEL)
        arcade.draw_lrbt_rectangle_filled(20, 231, 230, 235, ORANGE)
        arcade.draw_lrbt_rectangle_filled(237, 241, 25, 245, PURPLE)
        self._text("ROUND COMPLETE", 32, 210, GOLD, 15, ORANGE)
        self._text("YOUR SCORE", 32, 190, BLUE, 11)
        self._text(str(score), 30, 131, WHITE, 43)
        self._line(32, 117, 220, 117, PANEL_LIGHT, 2)
        self._text("YOUR RANK", 32, 90, BLUE, 11)
        self._text(f"#{rank}", 30, 43, GOLD, 39, ORANGE)
        self._text(f"OF {len(self.scores) + 1} TOTAL", 129, 54, MUTED, 10)

        self._text("ENTER INITIALS", 254, 236, GOLD, 15, ORANGE)
        for index in range(3):
            left = 252 + index * 54
            self._button(left, left + 47, 190, 227, PANEL, BLUE)
            if index < len(self.initials):
                self._draw_letter(self.initials[index], left + 23, 194, 29)
            else:
                self._text("_", left + 17, 199, WHITE, 20)
        self._button(418, 463, 190, 227, PANEL, PURPLE)
        self._text("DEL", 426, 202, WHITE, 9)
        self._button(252, 463, 149, 182,
                     ORANGE if len(self.initials) == 3 else PANEL,
                     GOLD if len(self.initials) == 3 else PANEL_LIGHT)
        self._text("SAVE SCORE", 314, 159, BACKGROUND if len(
            self.initials) == 3 else MUTED, 13)

        for letter, left, bottom, right, top in letter_keys():
            self._button(left, right, bottom, top, PANEL, PANEL_LIGHT)
            self._draw_letter(letter, (left + right) / 2, bottom + 4, 19)

    def _draw_leaderboard(self) -> None:
        self._text("LEADERBOARD", 113, 235, GOLD, 24, ORANGE)
        self._text("RANK", 27, 212, MUTED, 10)
        self._text("TAG", 87, 212, MUTED, 10)
        self._text("SCORE", 198, 212, MUTED, 10)
        top_five = sorted(self.scores, key=lambda row: int(
            row["score"]), reverse=True)[:5]
        if not top_five:
            self._text("NO SCORES YET", 56, 151, MUTED, 14)
        for index, row in enumerate(top_five):
            y = 181 - index * 30
            rank_color = (GOLD, SILVER, BRONZE)[index] if index < 3 else BLUE
            arcade.draw_lrbt_rectangle_filled(21, 311, y - 4, y + 26,
                                              PANEL if index % 2 == 0 else BACKGROUND)
            arcade.draw_lrbt_rectangle_filled(21, 26, y - 4, y + 26,
                                              rank_color)
            self._text(f"{index + 1}", 31, y, rank_color, 14)
            self._text(row.get("initials") or "---", 86, y, WHITE, 14)
            self._text(row["score"], 198, y, ORANGE, 14)

        arcade.draw_lrbt_rectangle_filled(323, 466, 71, 204, PANEL)
        qr_size = self.leaderboard_qr.width * 3
        arcade.draw_texture_rect(self.leaderboard_qr, LBWH(
            397 - qr_size / 2, 83, qr_size, qr_size), pixelated=True)
        self._text("ONLINE LEADERBOARD", 334, 55, BLUE, 9)
        self._button(120, 360, 8, 45, ORANGE, GOLD)
        self._text("PLAY AGAIN", 171, 20, BACKGROUND, 16)

    def on_draw(self) -> None:
        self.clear()
        readings, error = self.poller.snapshot()
        self._update_peak(readings)

        self.canvas.framebuffer.clear(color=(*BACKGROUND, 255))
        with self.canvas.camera.activate():
            self._chrome()
            if self.view_state == "start":
                self._draw_shower(80, 90, 180, 60)
                ready = bool(readings) and self.poller.room_reference() is not None
                self._text("SHOWERHACKS CHALLENGE", 152, 234, BLUE, 10)
                self._text("SHOWERMASTER", 113, 186, RED, 29)
                self._text("SHOWERMASTER", 111, 189, GOLD, 29, ORANGE)
                self._text("TAKE THE CHALLENGE TO SEE HOW MUCH YOU NEED A SHOWER",
                           26, 166, MUTED, 11)
                self._button(80, 400, 75, 150,
                             ORANGE if ready else PANEL,
                             GOLD if ready else PANEL_LIGHT)
                self._text("START" if ready else "WAITING FOR SENSOR", 172 if ready else 118,
                           99, BACKGROUND if ready else WHITE, 29 if ready else 17)
                score_text = str(self.best_score)
                by_text = f"BY {self.best_initials}"
                label_width = bitmap_text("BEST SCORE", BLUE, 9).width
                score_width = bitmap_text(score_text, GOLD, 20, ORANGE).width
                by_width = bitmap_text(by_text, BLUE, 10).width
                score_left = (CANVAS_WIDTH - label_width - score_width - by_width - 24) // 2 + label_width + 12
                self._text("BEST SCORE", score_left - label_width - 12, 54, BLUE, 9)
                self._text(score_text, score_left + 2, 47, PURPLE, 20)
                self._text(score_text, score_left, 49, GOLD, 20, ORANGE)
                self._text(by_text, score_left + score_width + 12, 54, BLUE, 10)
                self._button(140, 340, 8, 45, PANEL, BLUE)
                self._text("LEADERBOARD", 184, 20, WHITE, 12)
            elif self.view_state == "ready":
                arcade.draw_lrbt_rectangle_filled(20, 231, 26, 245, PANEL)
                arcade.draw_lrbt_rectangle_filled(20, 231, 239, 245, BLUE)
                self._draw_underarm()
                arcade.draw_lrbt_rectangle_filled(237, 241, 25, 245, PURPLE)
                self._text("GET READY!", 258, 205, GOLD, 24, ORANGE)
                self._text("HOLD THE WAND UP TO THE", 270, 183, BLUE, 10)
                self._text("FUNKIEST PART OF YOURSELF", 262, 167, BLUE, 10)
                ready_seconds = ceil(self.ready_remaining_seconds())
                self._text(str(ready_seconds), 330, 76,
                           ORANGE if ready_seconds <= 3 else WHITE, 66)
                self._text("SECONDS", 317, 56, MUTED, 12)
            elif self.view_state == "meter":
                self._text("SMELL METER", 20, 238, GOLD, 14, ORANGE)
                self._button(340, 467, 190, 255, PANEL, ORANGE)
                remaining = self.remaining_seconds()
                timer_color = ORANGE if remaining <= 5 and int(
                    (5 - remaining) * 4) % 2 == 0 else WHITE
                countdown = self.countdown_display()
                self._text(countdown, 365 if len(countdown) ==
                           4 else 375, 208, timer_color, 32)
                self._text("SECONDS LEFT", 360, 194, GOLD, 9)

                arcade.draw_lrbt_rectangle_filled(20, 170, 96, 231, PANEL)
                arcade.draw_lrbt_rectangle_filled(20, 170, 225, 229, BLUE)
                self._draw_nose()

                round_score = score_for(
                    self.peak_tvoc, self.score_lower_bound_ppb)
                elapsed = 0.0 if self.round_started_monotonic is None else \
                    monotonic() - self.round_started_monotonic
                self._text(str(displayed_score(round_score, elapsed)), 182, 174, GOLD, 42, ORANGE)
                self._text("SCORE", 184, 159, BLUE, 10)
                if readings:
                    latest = readings[-1]
                    current = latest.tvoc_ppb
                    current_text = f"TVOC  {current} PPB"
                else:
                    current = 0
                    current_text = "TVOC  -- PPB"
                self._line(180, 150, 458, 150, PANEL_LIGHT, 2)
                self._text(current_text, 182, 128, GOLD, 12)
                self._text(f"PEAK  {self.peak_tvoc} PPB", 182, 108, MUTED, 10)
                filled = displayed_meter_segments(round_score, elapsed)
                for index in range(20):
                    left = 21 + index * 22
                    color = (BLUE if index < 5 else PURPLE if index < 10 else
                             ORANGE if index < 15 else RED if index < 18 else GOLD)
                    if index >= filled:
                        color = PANEL
                    arcade.draw_lrbt_rectangle_filled(
                        left, left + 19, 70, 94, color)
                    arcade.draw_lrbt_rectangle_filled(
                        left, left + 19, 90, 94,
                        WHITE if index < filled else PANEL_LIGHT)
                self._text(
                    f"START LINE {self.score_lower_bound_ppb} PPB", 21, 57, MUTED, 8)
                self._text(
                    f"ROOM {self.room_reference_ppb} PPB  /  RECENT TVOC", 21, 46, MUTED, 8)
                self._draw_trend(readings, 23, 42)
            elif self.view_state == "initials":
                self._draw_initials()
            else:
                self._draw_leaderboard()

            if error:
                self._text(error[:75], 20, 5, (255, 120, 120), 7)

        self.default_camera.use()
        self.canvas.draw()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display SGP30 TVOC readings in Arcade")
    parser.add_argument("--port",
                        help="Pico USB serial port, such as /dev/ttyACM0")
    parser.add_argument("--dev", action="store_true",
                        help="Use simulated TVOC readings without a Pico")
    parser.add_argument("--windowed", action="store_true",
                        help="Preview in a window instead of full screen")
    parser.add_argument("--scale", type=int, default=1,
                        help="Integer pixel scale in windowed mode (default: 1)")
    parser.add_argument("--threshold", type=int,
                        default=DEFAULT_THRESHOLD_PPB, help="TVOC score threshold in ppb")
    parser.add_argument("--scores-file", type=Path,
                        default=Path("showerhacks_scores.csv"), help="Round CSV path")
    parser.add_argument("--baseline-file", type=Path, default=Path(
        "sgp30_baselines.jsonl"), help="Sensor baseline log path")
    parser.add_argument("--room-history-file", type=Path, default=Path(
        "showerhacks_room_history.jsonl"), help="Five-minute room history path")
    args = parser.parse_args()
    if not args.dev and not args.port:
        parser.error("--port is required unless --dev is used")
    if args.threshold < 0:
        parser.error("--threshold must be nonnegative")
    if args.scale < 1 or (args.scale != 1 and not args.windowed):
        parser.error(
            "--scale must be a positive integer and requires --windowed")

    poller = SensorPoller(DemoSensor if args.dev else lambda: PicoSensor(args.port),
                          baseline_file=None if args.dev else args.baseline_file,
                          room_history_file=None if args.dev else args.room_history_file)
    window = TVOCWindow(
        poller,
        fullscreen=not args.windowed,
        window_scale=args.scale,
        threshold_ppb=args.threshold,
        scores_file=args.scores_file,
        s3_bucket=None if args.dev else os.environ.get("SHOWERHACKS_S3_BUCKET"),
    )
    poller.start()
    try:
        arcade.run()
    finally:
        poller.stop()
