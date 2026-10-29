# T6 live probe, part local (29 Oct 2026)

Approved by the user ("Approve, run local"), then amended at the user's
direction ("Use E2B instead", `d45464c`) because the server had
`gemma-4-E2B-it-GGUF` loaded rather than the reviewed E4B. Windows
controller, Unsloth Studio at 127.0.0.1:8888. Scope
`inventory-probe-local-v1`: 8 settled chat calls, **$0** (all 4 perception
calls served from the part-meta cache), no unknown calls.

- **View check passed:** E2B called `view` on photo 1 and replied "Photo 1
  shows two round baking pans sitting on a stovetop" — correct, so view
  images after tool messages work on the local server too.
- **Agent run on coco-calibration-04 failed:** after 5 short turns (17-298
  output tokens each; input 1,080-1,364 tokens), the 6th reply ran to the
  4,096-token output limit (`finish_reason` length) and the client raised
  "provider returned an incomplete response". The run's trace was lost because
  `run_agent` let that error propagate; fixed separately so a truncated reply
  ends the run as a stopped item with its trace.

Reading: the smallest Gemma can use the tools and see images, but can
derail into runaway generation on a multi-step task — a concrete target for
the harness (and a reason to cap output per turn in E-C). Not capability
evidence; one sample.
