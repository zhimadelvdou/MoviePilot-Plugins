# Manko manual search ambiguity

## Context

The live Manko search for `ADN-765` returned two complete detail records with distinct native IDs. Both records carry exact `ADN-765` values in `title_display_name`; neither has an explicit number/code/serial field. The records differ in `category_id`, while the observed metadata did not establish which one is the intended product or version.

## Contract

- Explicit manual `search_medias` and `async_search_medias` calls for the selected Manko source may return every distinct, fully validated native ID whose detail proves the requested exact number.
- Sync and async manual results preserve the Manko source and each native ID. Duplicate rows with the same native ID are deduplicated.
- Automatic `recognize_media`, `async_recognize_media`, scoped recognition, and provider search retain fail-closed uniqueness: multiple matching native IDs produce no selected record.
- Manual multi-result searches do not populate the shared `manko:number:<number>` cache with an arbitrary candidate. A later explicit native-ID lookup can resolve that ID without making automatic number recognition select it.
- Search completeness, valid IDs, detail identity and number evidence, source selection, generation checks, and async nonblocking behavior remain required. Other sources and their search semantics do not change.

## Validation

Use the real MoviePilot V3 SDK with the repository's offline HTTP replay boundary and a minimal two-record Manko fixture. No live service access is part of the test suite.
