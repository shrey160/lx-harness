# Third-party notices

Original LX code and documentation are licensed under [Apache-2.0](LICENSE).
This does not replace licenses for third-party dependencies, imported examples,
benchmark assets, datasets, user-provided media or material marked otherwise.

## Separate execution core

LX installs the separately maintained
[Harness2 portable core](https://github.com/shrey160/harness2-portable-core).
Its original code uses Apache-2.0, as does its vendored independent `harness2-cli`
engine. The core preserves upstream attribution, local patch provenance and
third-party licenses. See its
[third-party notices](https://github.com/shrey160/harness2-portable-core/blob/main/THIRD_PARTY_NOTICES.md)
and [upstream record](https://github.com/shrey160/harness2-portable-core/blob/main/UPSTREAM.md).

The core's optional benchmark plugin includes **WorkBuddy-derived material under
separate terms with an EU-use restriction**. That material is not Apache-2.0.
The root LX license does not remove those conditions. Check the core's notices
before using or redistributing that optional material; LX's ordinary task loop
does not require the benchmark plugin.

## Dependencies and external material

The bundled [three-sample inventory example](examples/inventory-cash-three/MEDIA.md)
contains COCO-derived CC BY 2.0 cutouts, user-supplied photographs and synthetic
Kokoro speech. Its asset notices and attribution remain with the dataset; the
root code license does not relicense all media.

Python and frontend dependencies retain their own licenses. Dependency versions
are recorded in [pyproject.toml](pyproject.toml), [uv.lock](uv.lock),
[web/package.json](web/package.json) and [web/package-lock.json](web/package-lock.json).
Preserve applicable dependency license/copyright notices when redistributing them
or built frontend bundles.

API providers, model weights and datasets have their own terms; Apache-2.0 does
not grant access to a hosted service or relicense a model. Experiment evidence can
contain third-party or user-supplied inputs/media; the root license does not grant
rights the contributors do not own. Preserve any asset-specific attribution and
verify the source's terms before reusing such content.
