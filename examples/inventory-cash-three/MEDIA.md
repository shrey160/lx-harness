# Media provenance and terms

Original LX code, task/check, constructed JSON and documentation retain the
repository's [Apache-2.0 license](LICENSE.txt). Photographic source material retains its own
terms. Keep this notice and `attribution.json` with copies of the dataset.

## COCO-derived composite scenes

Sample-01 and sample-02 photos 1 and 2 use twelve distinct object cutouts from
COCO 2017 source photographs. The COCO annotation metadata labels each source
**Attribution License**, license ID 4, with the
[CC BY 2.0 license](https://creativecommons.org/licenses/by/2.0/).
Objects were cut along annotation polygons, resized and placed onto generated
backgrounds; these are composites, not photographs of real transactions.

`attribution.json` preserves each source's COCO image ID, annotation ID, original
filename, source image URLs, Flickr source-page link and license. COCO metadata
does not supply photographer names; the original source-page links identify the
photographs rather than inventing creator credit. Preserve any additional
attribution supplied by those sources when reusing their photographs.

| Composite | COCO image IDs / source photographs |
| --- | --- |
| sample-01 / photo 1 | [322125](https://www.flickr.com/photo.gne?id=3757920811), [158498](https://www.flickr.com/photo.gne?id=2967497897), [257941](https://www.flickr.com/photo.gne?id=7857283620) |
| sample-01 / photo 2 | [371873](https://www.flickr.com/photo.gne?id=3926739165), [396608](https://www.flickr.com/photo.gne?id=8654786106), [568788](https://www.flickr.com/photo.gne?id=967049911), [336762](https://www.flickr.com/photo.gne?id=211104439) |
| sample-02 / photo 1 | [419942](https://www.flickr.com/photo.gne?id=8618024298), [214618](https://www.flickr.com/photo.gne?id=9053517233) |
| sample-02 / photo 2 | [388031](https://www.flickr.com/photo.gne?id=5133611496), [571804](https://www.flickr.com/photo.gne?id=6155496426), [86282](https://www.flickr.com/photo.gne?id=5349666143) |

## User-supplied photographs

Shrey supplied four photographs for these examples: 31130.jpg (stationery),
31131.jpg (cutlery), 31132.jpg (scissors/cups) and 31133.jpg (chocolates/drinks).
They are included here for the requested reusable test example. No separate
open-media license was specified for these photographs; the code license does
not relicense them. Product names and packaging remain their respective owners'.

Only normalized derivatives are included: upright EXIF orientation, proportional
768×1024 resize and metadata removal, without cropping or content synthesis.
Original filenames/hashes and processing descriptions remain in `attribution.json`;
machine-local source paths and original metadata are not included.

## Conversational speech

The three clips were synthesized offline with Kokoro v1.0, using af_sarah,
am_adam and af_sarah respectively. They are not recordings of a person or
actual shop transactions. The [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M)
identifies its weights as Apache-2.0 and records its acknowledgements. Model
weights, voice packs and speech-engine dependencies are not distributed here.
`reviewer/voice-scripts.json` records the authored scripts for audit; do not give
them to the worker instead of using the supplied audio through Transcribe.
