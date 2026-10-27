# Synthetic images + transcripts demo contract

## Dataset

lx.domain.build_demo_dataset(path) writes four deterministic 96 × 64 RGB PNGs. Each image contains two solid colored rectangles. The images are painted from the same rectangle definitions used to author controller truth, so labels and pixel boxes are known by construction; no vision model or OCR step creates the expected answers. Transcript strings are supplied text attached to each sample.

The split is assigned at the source-scene level: dev-scene-01 and dev-scene-02 belong to dev; heldout-scene-01 and heldout-scene-02 belong to heldout. A scene cannot appear in both splits.

Worker-visible material is confined to worker_inputs/: PNGs and manifest.json records containing sample ID, source scene ID, split, relative image path, and supplied transcript. Controller truth is written separately to controller_truth/ground_truth.json. The worker manifest contains no object labels or boxes. Keep the controller truth directory outside any worker mount or editable surface.

## Output schema

The candidate output must be a JSON object with exactly these fields:

    {
      "objects": [
        {"label": "red_square", "box": [8, 10, 31, 33]}
      ],
      "text": "A red square sits beside a blue rectangle."
    }

Each object has exactly label (string) and box (four finite JSON numbers in [x1, y1, x2, y2] order). Coordinates are pixel edges, with the right and bottom edges exclusive. Boxes must be ordered, non-empty, and within the 96 × 64 image. Booleans, numeric strings, non-finite values, missing fields, and extra fields are invalid. Object order does not matter; objects must match one-to-one by label.

lx.domain.verify_output(controller_truth, candidate) accepts each box only when every corresponding edge differs from truth by at most **1 pixel**. Text must equal the supplied transcript exactly. This is a deterministic structured-output check, not a pixel-perfect rendering score or a claim of model perception quality.

## Gate evidence and limits

The unit tests exercise known-good acceptance and reject missing objects, boxes shifted beyond tolerance, wrong transcript text, malformed/extra fields, NaN, and boolean coordinates. They also check PNG signatures/chunk CRCs, dimensions, scene-level split integrity, and that the worker manifest omits truth. No network or model calls are made. Passing this gate establishes only the local dataset/verifier contract; it does not demonstrate live multimodal capability.
