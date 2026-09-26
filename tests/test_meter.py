import unittest

from showerhacks.app import GOLD, ORANGE, bitmap_text, meter_segments, nose_frame_index, projected_rank


class MeterTest(unittest.TestCase):
    def test_large_increases_remain_distinct(self) -> None:
        self.assertEqual(meter_segments(0), 0)
        self.assertLess(meter_segments(500), meter_segments(1000))
        self.assertLess(meter_segments(1000), 20)

    def test_projected_rank_places_ties_after_saved_scores(self) -> None:
        scores = [{"score_ppb": "100"}, {"score_ppb": "50"}]
        self.assertEqual(projected_rank(101, scores), 1)
        self.assertEqual(projected_rank(100, scores), 2)
        self.assertEqual(projected_rank(75, scores), 2)
        self.assertEqual(projected_rank(50, scores), 3)

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
