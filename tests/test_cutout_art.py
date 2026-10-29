import random
import time
import unittest

from PIL import Image

from lx.cutout_art import BACKGROUND_STYLES, background, colour_name, dominant_colour
from lx.inventory import COLOURS

RED, BLUE = (255, 0, 0), (0, 0, 255)


class ColourNameTests(unittest.TestCase):
    def test_named_colours(self):
        cases = {(230, 30, 30): "red", (240, 150, 30): "orange", (120, 70, 30): "brown", (90, 35, 25): "brown", (140, 20, 25): "red",
                 (240, 220, 40): "yellow", (40, 170, 70): "green", (40, 90, 220): "blue",
                 (140, 60, 180): "purple", (240, 150, 200): "pink", (20, 20, 20): "black",
                 (250, 250, 250): "white", (128, 128, 128): "grey", (180, 160, 150): None}
        for rgb, name in cases.items():
            with self.subTest(rgb=rgb):
                self.assertEqual(colour_name(*rgb), name)

    def test_non_none_results_are_in_colours(self):
        for r in range(0, 256, 17):
            for g in range(0, 256, 17):
                for b in range(0, 256, 17):
                    name = colour_name(r, g, b)
                    if name is not None:
                        self.assertIn(name, COLOURS)


class DominantColourTests(unittest.TestCase):
    def test_solid_red(self):
        self.assertEqual(dominant_colour(Image.new("RGBA", (40, 40), RED + (255,))), "red")

    def test_half_red_half_blue_is_none(self):
        img = Image.new("RGBA", (40, 40), RED + (255,))
        img.paste(BLUE + (255,), (20, 0, 40, 40))
        self.assertIsNone(dominant_colour(img))

    def test_transparent_surroundings_are_ignored(self):
        img = Image.new("RGBA", (40, 40), BLUE + (0,))
        img.paste(RED + (255,), (10, 10, 30, 30))
        self.assertEqual(dominant_colour(img), "red")

    def test_too_few_pixels_is_none(self):
        self.assertIsNone(dominant_colour(Image.new("RGBA", (5, 5), RED + (255,))))


class BackgroundTests(unittest.TestCase):
    def test_size_mode_and_determinism_per_style(self):
        for style in BACKGROUND_STYLES:
            with self.subTest(style=style):
                img = background(320, 240, random.Random(7), style=style)
                self.assertEqual(img.size, (320, 240))
                self.assertEqual(img.mode, "RGB")
                again = background(320, 240, random.Random(7), style=style)
                self.assertEqual(img.tobytes(), again.tobytes())

    def test_random_style_is_deterministic(self):
        first = background(320, 240, random.Random(7))
        second = background(320, 240, random.Random(7))
        self.assertEqual(first.tobytes(), second.tobytes())

    def test_not_flat(self):
        for style in BACKGROUND_STYLES:
            with self.subTest(style=style):
                img = background(320, 240, random.Random(7), style=style)
                self.assertGreater(max(hi - lo for lo, hi in img.getextrema()), 0)

    def test_large_render_is_fast(self):
        start = time.perf_counter()
        background(1024, 768, random.Random(7))
        self.assertLess(time.perf_counter() - start, 1.5)


if __name__ == "__main__":
    unittest.main()
