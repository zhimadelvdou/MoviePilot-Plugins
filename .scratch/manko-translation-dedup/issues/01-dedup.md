# Deduplicate complementary Manko translations

Type: task
Status: resolved
Blocked by: none

## Goal

Treat multiple exact-number Manko records as one work when the complete candidate group agrees on all non-translation metadata and actors, and its title/description translations complement one another without same-language conflicts.

## Acceptance criteria

- A synthetic SDK-boundary test with two exact-number records, identical complete actors and all metadata equal except that the second record adds a Thai `discription` translation proves the intended behavior through sync and async provider `search` and `search_matches`.
- The canonical packet uses the minimum native `_id`, includes the Thai translation, and leaves original fixture records unchanged.
- Conflicting same-language translation bodies, any other compared-field difference, incomplete/conflicting actors, and group-wide non-transitive differences remain ambiguous; translation comparison is exact and does not normalize or rewrite text.
- Plugin manual search returns one stable Manko candidate and automatic recognition selects it for the same equivalent group; true conflicts remain manual choices and are not auto-selected.
- Sync/async provider and plugin paths agree. Existing number/page/detail/actor validation, transport rules, cache format, source adapters, and automatic configuration remain intact.
- Version and package manifest advance to 0.8.3, README reflects the observable behavior, and existing 0.8.2 artwork/runtime behavior plus `metadata-v2` cache format remain unchanged.
- Root runs focused red before implementation and the approved isolated SDK suite after implementation. Production acceptance is a separate native preview → execute once → status and file audit; do not force a native ID when automatic identification remains ambiguous.

## Scope and safety

Only `plugins.v3/xiaojiejiescraping` provider/plugin source, its focused tests, README, package manifest, and this issue/spec are in scope. Do not change MoviePilot core, frontend/dist, other source adapters, automatic configuration or ledger schema, Inputs, or metadata cache format. Use synthetic translation text in tests and documentation; do not copy live adult titles, plot text, or actor data.

## Test seam

Exercise the public `MankoProvider.search`, `async_search`, `search_matches`, and `async_search_matches` methods using the established SDK request boundary replay. Keep all network calls mocked at the HTTP transport boundary; no live socket access.

## Comments

- Root observed two live exact-number ADN-775 IDs with matching non-translation metadata and complete actor JSON; the records differ by the second record's additional Thai description translation. The live evidence is recorded by structure only here, without product title, plot, or actor text.

## Answer

Released as 0.8.3. The provider now merges complementary title and description translations only when the full candidate group agrees on all other checked metadata and complete actor JSON. Same-language conflicts, malformed or duplicate translation entries, missing group-wide language consistency, and any other record or actor difference remain ambiguous. Flat-title records with a missing or empty `title_display_name` remain usable by plugin manual search and recognition. Sync/async provider and plugin paths share the behavior.

The isolated official V3 SDK suite passed with `1544 tests` and `7 subtests`. Live verification on 2026-10-23 found one canonical Manko result for each sample: ADN-775 resolved to `69ec868860ed2e0c51558e54`, and ADN-765 to `69a07400131836087e481b7a`. ADN-775 was identified in the native manual preview without entering an ID, executed once, and read back as `completed` with `file_transfer=1/1/0`. The final audit confirmed the source SHA-256, size, device, inode, and mtime were unchanged; the target is a hard link to the source. Five target files were present: the video, NFO, poster, fanart, and backdrop. NFO identity and expected metadata fields were complete; poster was 800×538 and the horizontal fanart/backdrop was 800×534. The background image hash differed from the poster, while fanart and backdrop hashes matched. Existing ADN-765 target data and unrelated ledgers/configuration were preserved. The real qBittorrent new-download automatic flow remains unverified.
