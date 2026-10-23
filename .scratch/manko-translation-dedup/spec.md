# Manko language-translation duplicate resolution

## Problem and observed evidence

Manko returns multiple native IDs for one exact catalog number. The live ADN-775 observation contains IDs `69ec868860ed2e0c51558e54` and `69f56f7f646be358998d7f87`. Their other compared record metadata and complete actor JSON match; the `discription` field has four shared language entries and the second record adds one Thai entry (83 characters). Runtime, share date, and artwork counts also match. The synthetic regression below reproduces only this structure and uses no real title, plot, or actor text.

## Contract

- At the provider boundary, `search` and `async_search` may resolve a multi-ID exact-number result when the full candidate group is equivalent except for complementary translated `title_display_name` and/or `discription` entries.
- For `title_display_name`, compare translation bodies in `title`; for `discription`, compare bodies in `overview`. Same-language bodies must match exactly. Missing languages may be added to a deep-copied canonical record. Each non-empty translation field must share at least one consistent language across the group. Conflicting duplicate languages within one record, unknown per-item fields that would be dropped, malformed language items, or same-language conflicts remain ambiguous.
- Every other record field remains strictly equivalent after only the established native-ID and transport-only exclusions. Candidate equivalence is group-wide, not a chain of pairwise matches. Complete actor response JSON must remain identical and complete for every candidate.
- If the whole group is equivalent, return one packet with the lexicographically smallest native `_id`, preserving all non-conflicting translations. Do not mutate fetched source records or shared caches.
- `search_matches` and `async_search_matches` return one synthesized packet only when the whole exact-match group passes the same equivalence checks; genuine conflicts remain multiple manual choices. Sync and async behavior must agree.
- Preserve exact-number validation, complete search pagination, native-ID/detail verification, actor completeness, anonymous transport behavior, and all non-Manko source behavior.
- Plugin-level manual search and automatic recognition must then observe the same deduplicated candidate: manual search exposes one stable source ID, and automatic recognition can select that ID. Ambiguous or conflicting records remain available for manual choice and are not auto-selected.

## Test and acceptance boundaries

Provider regression tests use the existing MoviePilot SDK HTTP boundary with synthetic replay responses and no real socket access. The focused first vertical test covers same-number records where only the second record adds a Thai description translation, while returning consistent actors. It exercises sync/async `search` and `search_matches`, stable ID selection, translation retention, and raw-fixture immutability.

Before implementation, Root runs the focused selector under the official isolated SDK/offline runner and records the expected red result. After the minimal merge implementation, validate provider sync/async paths, conflicting translations and non-translation metadata, group-wide conflicts, plugin manual search/recognition, then native preview → single execute → status and target-file audit. Live acceptance was completed for ADN-775 and the search result was checked for ADN-765 as recorded below. This does not establish automatic organization of newly completed qBittorrent downloads. No duplicate-specific live ID override or relaxation of Manko identity rules is allowed.

Target release: 0.8.3. Keep the existing 0.8.2 artwork/runtime behavior and `metadata-v2` cache format.

## Verification

- Isolated official MoviePilot V3 SDK regression: 1544 tests and 7 subtests passed.
- Live search: ADN-775 returned canonical ID `69ec868860ed2e0c51558e54`; ADN-765 returned canonical ID `69a07400131836087e481b7a`.
- ADN-775 native manual preview identified the work without a supplied media ID; one execute completed with `file_transfer=1/1/0`.
- Final audit verified source SHA-256, size, device/inode, and mtime unchanged; target and source share an inode. NFO identity and metadata fields were complete, and the poster/background images were present and distinct by SHA-256.
- Automatic qBittorrent new-download behavior remains unverified.
