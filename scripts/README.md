# scripts

Run these from `main_LX`.

| File | Purpose |
|---|---|
| `serve.ps1` | Starts the app from PowerShell: natively with `.venv-win` if it exists, otherwise in WSL. Takes `-Port`, `-Wsl` and `-Distro` |
| `build_sandbox_image.py` | Builds the Docker sandbox image from pinned inputs. Needs Docker Desktop running |
| `validate_product.py` | Runs the offline tests and saves evidence without machine paths or environment dumps |
| `validate_records.py` | Runs five full-size offline records checks and exports safe evidence |
| `build_inventory_mix.py` | Builds the `inventory-mix-v1` dataset in three steps |
| `inventory_mix_rejects.json` | Cutouts rejected by eye for that dataset |
| `export_samples.py` | Copies a frozen inventory dataset's split into a new samples folder |
| `synthesize_voicecount.py` | Voices typed requests offline with Kokoro; also used for inventory requests |

None of these spends money or calls a provider. The dataset scripts need local
downloads that are gitignored under `.lx/`.
