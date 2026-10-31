# evidence

Saved results of tests and live runs, one folder per run, named by date and subject.
These are records, so they are not edited afterwards. A folder holds reports with
hashes, scores and usage; keys and machine paths are kept out.

Preview and offline folders use doubles and prove plumbing only. Only live folders say
anything about a model.

| Folder (dates in 2026) | What it holds | Kind |
|---|---|---|
| `10-28-product-gates` | Product gate tests | Offline |
| `10-28-app-preview` | App preview, core regression and browser check | Preview |
| `10-28-live-gate` | S1 live JSON spike: correct result | Live |
| `10-28-live-app` | O-11 app run: base retained | Live |
| `10-28-records-offline` | Records wiring trials | Offline |
| `10-28-records-calibration` | Records calibration: 40/40, no headroom | Live |
| `10-29-eventlog-offline`, `10-29-eventlog-calibration-request` | Event-log wiring and request | Offline |
| `10-29-eventlog-calibration` | Event-log ladder: 40/40, no headroom | Live |
| `10-29-voicecount-offline` | Voicecount wiring | Offline |
| `10-29-voicecount-probe-meta`, `10-29-voicecount-probe-local` | S5 probes | Live |
| `10-29-voicecount-calibration` | S6: base harness 0.80, Spark alone 0.45 | Live |
| `10-29-voicecount-trials-stopped` | S7, stopped by the user | Live |
| `10-29-inventory-preview` | Skills and scripts in the preview loop | Preview |
| `10-29-inventory-probe-meta`, `10-29-inventory-probe-local` | T6 probes | Live |
| `10-29-inventory-calibration` | T7: base 0.39, gate passed | Live |
| `10-29-inventory-ea` | T8 stream: 0.31 to 0.66 | Live |
| `10-29-inventory-eval` | E-B: held-out 0.47 to 0.70, criterion met | Live |
| `10-31-spark-calibration` | Spark on the new loop: base 0.82, loop 0.82, 6 of 6 answered | Live |
| `10-29-inventory-eval-local` | E-C, stopped on a controller timeout | Live |
| `10-30-inventory-eval-local-retry` | E-C retry: 0.40 to 0.30, criterion not met | Live |
| `10-30-inventory-agent-probe`, `10-30-inventory-agent-probe-v2` | G1b probes of a swapped agent | Live |
| `10-30-inventory-check` | Saved answers rescored by the check file: no differences | Offline |
| `10-30-nvidia-probe`, `10-30-openrouter-probe` | Muse Glimmer probes: 3/3 each | Live |
| `10-30-nvidia-demo` | Glimmer on NVIDIA: failed on old time limits | Live |
| `10-30-openrouter-demo` | Glimmer on the pixel-box demo: no improvement | Live |
| `10-30-openrouter-inventory` | Glimmer on inventory: 0.00, agent never answered | Live |
| `10-31-openrouter-rerun-stopped` | Glimmer rerun, stopped by the user in the first sample: no score | Live |
