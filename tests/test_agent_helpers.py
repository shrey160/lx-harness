import copy
import unittest

from lx.agent import agent_tools, count_boxes, in_region, iou, tool_identity

SEG = {"kind": "segmentation", "name": "SAM 3.1", "capabilities": ["finds objects"]}
ASR = {"kind": "transcription", "name": "Transcribe", "capabilities": ["speech to text"]}


def b(x1, y1, x2, y2, c=None, w=100, h=100):
    box = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h}
    if c is not None:
        box["c"] = c
    return box


class IouTests(unittest.TestCase):
    def test_identical_boxes(self):
        self.assertEqual(iou(b(0, 0, 9, 9), b(0, 0, 9, 9)), 1.0)

    def test_disjoint_boxes(self):
        self.assertEqual(iou(b(0, 0, 9, 9), b(20, 20, 29, 29)), 0.0)

    def test_touching_edges_only(self):
        self.assertEqual(iou(b(0, 0, 9, 9), b(10, 0, 19, 9)), 0.0)

    def test_partial_overlap(self):
        self.assertAlmostEqual(iou(b(0, 0, 9, 9), b(5, 0, 14, 9)), 50 / 150)


class RegionTests(unittest.TestCase):
    def test_top_left_box(self):
        box = b(10, 10, 19, 19)
        self.assertTrue(in_region(box, "left"))
        self.assertTrue(in_region(box, "top"))
        self.assertFalse(in_region(box, "right"))
        self.assertFalse(in_region(box, "bottom"))

    def test_bottom_right_box(self):
        box = b(80, 80, 89, 89)
        self.assertTrue(in_region(box, "right"))
        self.assertTrue(in_region(box, "bottom"))
        self.assertFalse(in_region(box, "left"))
        self.assertFalse(in_region(box, "top"))

    def test_whole_always_matches(self):
        for box in (b(10, 10, 19, 19), b(80, 80, 89, 89), b(49, 0, 50, 9)):
            self.assertTrue(in_region(box, "whole"))

    def test_centre_on_midline_counts_as_right(self):
        box = b(49, 0, 50, 9)
        self.assertEqual((box["x1"] + box["x2"] + 1) / 2, 50.0)
        self.assertTrue(in_region(box, "right"))
        self.assertFalse(in_region(box, "left"))


class CountBoxesTests(unittest.TestCase):
    def test_defaults_count_everything(self):
        boxes = [b(0, 0, 9, 9), b(20, 20, 29, 29), b(80, 80, 89, 89)]
        self.assertEqual(count_boxes(boxes), {"count": 3, "region": "whole", "boxes_in": 3,
                                              "removed_low_confidence": 0, "removed_duplicates": 0})

    def test_min_confidence_drops_below_and_keeps_missing_confidence(self):
        boxes = [b(0, 0, 9, 9, c=0.49), b(20, 20, 29, 29, c=0.5), b(40, 40, 49, 49)]
        result = count_boxes(boxes, min_confidence=0.5)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["removed_low_confidence"], 1)

    def test_dedupe_merges_overlapping_boxes(self):
        boxes = [b(0, 0, 9, 9, c=0.9), b(1, 1, 10, 10, c=0.6)]
        merged = count_boxes(boxes, dedupe_iou=0.3)
        self.assertEqual(merged["count"], 1)
        self.assertEqual(merged["removed_duplicates"], 1)
        kept = count_boxes(boxes)
        self.assertEqual(kept["count"], 2)
        self.assertEqual(kept["removed_duplicates"], 0)

    def test_dedupe_keeps_more_confident_box(self):
        # Lower-confidence box listed first; its centre is on the left, the stronger one on the right.
        boxes = [b(30, 0, 49, 9, c=0.6), b(40, 0, 59, 9, c=0.9)]
        self.assertEqual(count_boxes(boxes, dedupe_iou=0.3, region="right")["count"], 1)
        self.assertEqual(count_boxes(boxes, dedupe_iou=0.3, region="left")["count"], 0)
        self.assertEqual(count_boxes(boxes, min_confidence=0.61, dedupe_iou=0.3, region="right")["count"], 1)

    def test_region_filtering(self):
        left = [b(0, 0, 9, 9), b(10, 10, 19, 19), b(20, 20, 29, 29)]
        right = [b(80, 80, 89, 89), b(60, 60, 69, 69)]
        boxes = left + right
        self.assertEqual(count_boxes(boxes, region="left")["count"], 3)
        self.assertEqual(count_boxes(boxes, region="right")["count"], 2)

    def test_invalid_arguments_raise(self):
        for kwargs in ({"min_confidence": -0.1}, {"min_confidence": 1.5}, {"dedupe_iou": "0.5"},
                       {"min_confidence": True}, {"region": "middle"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    count_boxes([], **kwargs)


class AgentToolsTests(unittest.TestCase):
    def test_segment_and_transcribe_tools(self):
        tools = agent_tools([SEG, ASR])
        names = {t["function"]["name"] for t in tools}
        self.assertEqual(names, {"segment", "transcribe", "count_boxes"})
        by_name = {t["function"]["name"]: t["function"] for t in tools}
        self.assertIn("SAM 3.1", by_name["segment"]["description"])
        self.assertIn("finds objects", by_name["segment"]["description"])
        self.assertEqual(by_name["segment"]["parameters"]["required"], ["concept"])
        self.assertEqual(by_name["transcribe"]["parameters"]["properties"], {})

    def test_transcription_only_has_no_count_boxes(self):
        self.assertEqual([t["function"]["name"] for t in agent_tools([ASR])], ["transcribe"])

    def test_no_entries_no_tools(self):
        self.assertEqual(agent_tools([]), [])


class ToolIdentityTests(unittest.TestCase):
    def test_stable_for_equal_lists_and_changes_with_description(self):
        tools = agent_tools([SEG, ASR])
        self.assertEqual(tool_identity(tools), tool_identity(agent_tools([SEG, ASR])))
        changed = copy.deepcopy(tools)
        changed[0]["function"]["description"] += " extra"
        self.assertNotEqual(tool_identity(tools), tool_identity(changed))


if __name__ == "__main__":
    unittest.main()
