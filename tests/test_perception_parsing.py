import base64
import io
import unittest
import wave

from lx.perception import (concept_text, format_boxes, image_type, multipart, parse_boxes, parse_transcript,
                           sam_body, sam_text, wav_duration_ms)
from lx.spark import ModelError

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 10
JPEG = b"\xff\xd8\xff" + b"0" * 10
EXAMPLE = ("<0f>0<|box;x1=353;y1=382;x2=453;y2=427;w=537;h=561|><|mask;x=0;y=0;data=46,101,~abc|>,"
           "1<|box;x1=0;y1=0;x2=10;y2=12;w=537;h=561|><|mask;x=0;y=0;data=1,~z|>")


def wav(rate=16000, channels=1, width=2, seconds=1.0):
    buffer = io.BytesIO()
    frames = int(rate * seconds)
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(b"\x00" * frames * channels * width)
    return buffer.getvalue()


def box(x1, y1, x2, y2, w=640, h=480, c=None):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h, **({"c": c} if c is not None else {})}


class ParseBoxesTests(unittest.TestCase):
    def test_documented_example_without_confidence(self):
        boxes = parse_boxes(EXAMPLE)
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0], {"x1": 353, "y1": 382, "x2": 453, "y2": 427, "w": 537, "h": 561})
        self.assertNotIn("c", boxes[0])

    def test_confidence_is_read(self):
        text = "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10;c=0.875|><|mask;x=0;y=0;data=0,~|>"
        self.assertEqual(parse_boxes(text)[0]["c"], 0.875)

    def test_empty_text_means_zero_matches(self):
        self.assertEqual(parse_boxes(""), [])

    def test_invalid_boxes_raise(self):
        bad = {
            "x2 at width": "<|box;x1=0;y1=0;x2=537;y2=1;w=537;h=561|>",
            "x1 after x2": "<|box;x1=50;y1=0;x2=10;y2=1;w=537;h=561|>",
            "missing h": "<|box;x1=0;y1=0;x2=1;y2=1;w=537|>",
            "confidence above 1": "<|box;x1=0;y1=0;x2=1;y2=1;w=537;h=561;c=1.5|>",
            "size mismatch": "<|box;x1=0;y1=0;x2=1;y2=1;w=537;h=561|>",
        }
        for name, text in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(ModelError):
                    parse_boxes(text, size=(100, 100) if name == "size mismatch" else None)

    def test_mask_payload_with_delimiters_is_not_a_box(self):
        text = ("<|box;x1=0;y1=0;x2=10;y2=12;w=537;h=561|>"
                "<|mask;x=0;y=0;data=1,~a|b;c<d|>")
        self.assertEqual(len(parse_boxes(text)), 1)

    def test_format_then_parse_round_trip(self):
        boxes = [box(1, 2, 30, 40, c=0.8125), box(5, 6, 70, 80, c=0.5), box(0, 0, 9, 9, c=0.999)]
        parsed = parse_boxes(format_boxes(boxes))
        self.assertEqual(len(parsed), 3)
        for original, back in zip(boxes, parsed):
            self.assertEqual({k: back[k] for k in ("x1", "y1", "x2", "y2", "w", "h")},
                             {k: original[k] for k in ("x1", "y1", "x2", "y2", "w", "h")})
            self.assertEqual(round(back["c"], 3), round(original["c"], 3))
        self.assertEqual(format_boxes([]), "")


class SamTextTests(unittest.TestCase):
    def test_top_level_output_text(self):
        self.assertEqual(sam_text({"output_text": "X"}), "X")

    def test_output_parts_are_joined(self):
        raw = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "A"}, {"type": "output_text", "text": "B"}]}]}
        self.assertEqual(sam_text(raw), "AB")

    def test_malformed_replies_raise(self):
        for raw in ({}, "text", None, []):
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(ModelError):
                    sam_text(raw)


class ConceptTests(unittest.TestCase):
    def test_normalises_case_and_spacing(self):
        self.assertEqual(concept_text("  Red   Apples "), "red apples")

    def test_rejects_invalid_concepts(self):
        for value in ("", "x" * 61, "apples<|point;x=1|>", "123 apples", None):
            with self.subTest(value=repr(value)[:20]):
                with self.assertRaises(ValueError):
                    concept_text(value)


class MediaTests(unittest.TestCase):
    def test_image_types(self):
        self.assertEqual(image_type(PNG), "image/png")
        self.assertEqual(image_type(JPEG), "image/jpeg")

    def test_rejected_images(self):
        for data in (b"GIF89a...", b"", b"\x89PNG\r\n\x1a\n" + b"0" * 2_000_000):
            with self.subTest(head=data[:8]):
                with self.assertRaises(ModelError):
                    image_type(data)

    def test_wav_durations(self):
        self.assertEqual(wav_duration_ms(wav(16000, seconds=1.0)), 1000)
        self.assertEqual(wav_duration_ms(wav(24000, seconds=0.5)), 500)

    def test_rejected_audio(self):
        for data in (wav(44100), wav(channels=2), wav(width=1), b"RIFFxxxx", b""):
            with self.subTest(head=data[:8]):
                with self.assertRaises(ModelError):
                    wav_duration_ms(data)


class WireTests(unittest.TestCase):
    def test_sam_body(self):
        body = sam_body(PNG, "Apples")
        self.assertEqual(body["model"], "sam-3.1")
        self.assertFalse(body["stream"])
        self.assertEqual(body["metadata"], {"include_confidence": "true"})
        content = body["input"][0]["content"]
        self.assertEqual([part["type"] for part in content], ["input_text", "input_image"])
        self.assertEqual(content[0]["text"], "apples")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        encoded = content[1]["image_url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), PNG)

    def test_parse_transcript(self):
        self.assertEqual(parse_transcript({"transcript": " hi ", "audioDurationMs": 1200}),
                         {"transcript": "hi", "duration_ms": 1200})

    def test_malformed_transcripts_raise(self):
        for raw in ({"audioDurationMs": 1200}, {"transcript": "hi", "audioDurationMs": 1200.0},
                    {"transcript": "hi", "audioDurationMs": -1}):
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(ModelError):
                    parse_transcript(raw)

    def test_multipart_body(self):
        body, content_type = multipart({"request": "{}", "audio": b"RIFF"}, boundary="b1")
        self.assertEqual(content_type, "multipart/form-data; boundary=b1")
        self.assertTrue(body.startswith(b"--b1\r\n"))
        self.assertIn(b'name="request"', body)
        self.assertIn(b'name="audio"; filename="audio.wav"', body)
        self.assertTrue(body.endswith(b"--b1--\r\n"))


if __name__ == "__main__":
    unittest.main()
