# Manko cover configuration and in-memory NFO verification

## Context

The deployed `0.3` manual search for `ADN-765` returns the two distinct Manko native IDs `69a07400131836087e481b7a` and `69a3697d1b1225b605c52f5f`. Root's live diagnostic found a valid HTTPS `cover_image`, while the deployment's `image_hosts` setting was empty; the existing exact-host check therefore filtered the image. This is a configuration cause, not a confirmed parser defect. In the inspected payloads, overview, date/year, and two actors are present. `video_duration` (integer), `tags` (list), `imdb_score` (string), and `rating` (string) also appear upstream but are not mapped; their units/semantics and user-facing meaning are not yet verified. No director was observed in the inspected payloads, which does not establish that upstream never supplies one. The differing `category_id` values remain semantically unknown. These observations describe this incident and are not universal or permanent upstream guarantees.

## Contract

- Treat the current cover finding as an exact-host configuration issue: the image allowlist is empty and the observed valid HTTPS cover host is not approved. Root manages any backed-up local configuration change; this task does not change it or add a default approved host.
- Verify the existing in-memory `MediaInfo` projection and NFO output for fields already mapped from the selected record: title, overview, date/year, actors, and cover only when the actual URL host is explicitly approved.
- Separate plugin URL acceptance from MoviePilot host download or disk persistence. Do not perform file operations or claim host-side image delivery from an in-memory check.
- Do not add mappings for `video_duration`, `tags`, `imdb_score`, or `rating` until their units, semantics, and MoviePilot display contract are verified. Do not invent director or any other missing metadata.
- Preserve the existing explicit manual search behavior: return both distinct exact native IDs, keep source/ID identity checks and deduplication, and do not populate an arbitrary shared `manko:number:` cache entry.
- Keep automatic recognition unique-only. Preserve unknown `category_id` semantics; do not infer a preferred record or category from the current evidence.
- Preserve exact-host URL validation and SSRF protections; the runtime allowlist may add only the observed host after a verified configuration backup. Do not enable discovery or modify media files.
- Do not enable discovery or perform file operations. Returning two search results alone is not complete artwork/metadata acceptance.
- Ensure other providers retain their existing behavior.

## Validation

This configuration-only correction was validated against the installed MoviePilot V3.1.6 / Python 3.14.7 runtime. The same native Manko search probe went from empty poster/backdrop fields to non-empty fields for both original IDs. A bounded anonymous image request returned a valid JPEG, and pure projection/NFO functions used an existing cached packet in memory without writing media files. The user confirmed the cover appeared after refresh.

Product code, tests and version remain unchanged at `0.3`; the earlier 1355-test suite was not rerun for this configuration/documentation change. Any future product-code change requires the real SDK isolated test workflow. Host-side NFO/image file persistence remains untested.
