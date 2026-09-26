import unittest

from showerhacks.app import GOLD, ORANGE, bitmap_text, displayed_meter_segments, load_scores, meter_segments, nose_frame_index, projected_rank, score_for, top_score_record
from pathlib import Path
from tempfile import TemporaryDirectory


class MeterTest(unittest.TestCase):
    def test_large_increases_remain_distinct(self) -> None:
        self.assertEqual(meter_segments(0), 0)
        self.assertLess(meter_segments(500), meter_segments(1000))
        self.assertLess(meter_segments(1000), 20)

    def test_meter_reveals_live_fill_over_five_seconds(self) -> None:
        target = meter_segments(1000)
        self.assertEqual(displayed_meter_segments(1000, 0), 0)
        self.assertEqual(displayed_meter_segments(1000, 2.5), target // 2)
        self.assertEqual(displayed_meter_segments(1000, 5), target)
        self.assertEqual(displayed_meter_segments(1000, 10), target)

    def test_projected_rank_places_ties_after_saved_scores(self) -> None:
        scores = [{"score": "100"}, {"score": "50"}]
        self.assertEqual(projected_rank(101, scores), 1)
        self.assertEqual(projected_rank(100, scores), 2)
        self.assertEqual(projected_rank(75, scores), 2)
        self.assertEqual(projected_rank(50, scores), 3)

    def test_top_score_keeps_the_initials_of_the_first_tie(self) -> None:
        self.assertEqual(top_score_record([]), (0, "---"))
        self.assertEqual(top_score_record([
            {"score": "67", "initials": "MJK"},
            {"score": "66", "initials": "ABC"},
            {"score": "67", "initials": "NEW"},
        ]), (67, "MJK"))

    def test_normalized_score_uses_fixed_logarithmic_scale(self) -> None:
        self.assertEqual(score_for(200, 240), 1)
        self.assertLess(score_for(740, 240), score_for(1240, 240))
        self.assertEqual(score_for(2240, 240), 100)

    def test_loads_old_ppb_scores_as_normalized_scores(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scores.csv"
            path.write_text("initials,score_lower_bound_ppb,peak_tvoc_ppb,score_ppb\n"
                            "ABC,240,1240,1000\n")
            rows = load_scores(path)
        self.assertEqual(rows[0]["score"], str(score_for(1240, 240)))

    def test_nose_intensifies_at_fifteen_and_five_seconds(self) -> None:
        self.assertEqual(nose_frame_index(24.0), 0)
        self.assertTrue(all(nose_frame_index(24 - tick / 10) in (0, 1, 2)
                            for tick in range(90)))
        self.assertEqual(nose_frame_index(15.0), 2)
        self.assertIn(3, {nose_frame_index(15 - tick / 10)
                          for tick in range(100)})
        self.assertEqual(nose_frame_index(5.0), 4)
        self.assertIn(5, {nose_frame_index(5 - tick / 10)
                          for tick in range(50)})

    def test_ui_text_has_hard_pixel_edges(self) -> None:
        texture = bitmap_text("SHOWERMASTER", GOLD, 29)
        self.assertEqual(set(texture.image.getchannel("A").getdata()), {0, 255})

    def test_gradient_text_keeps_hard_edges(self) -> None:
        image = bitmap_text("SHOWERMASTER", GOLD, 29, ORANGE).image
        self.assertEqual(set(image.getchannel("A").getdata()), {0, 255})
        colors = {pixel[:3] for pixel in image.getdata() if pixel[3]}
        self.assertGreater(len(colors), 2)


if __name__ == "__main__":
    unittest.main()
