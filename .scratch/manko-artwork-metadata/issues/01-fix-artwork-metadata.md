# Diagnose Manko cover configuration and verify in-memory NFO

Type: task
Status: resolved
Blocked by: none

## Goal

Document the confirmed Manko cover configuration cause and verify that the existing in-memory projection/NFO carries supported metadata. Do not expand product code, tests, or version `0.3` without separate evidence that an implementation defect remains.

## Acceptance criteria

- Record that the incident's valid HTTPS `cover_image` was excluded because the deployment had an empty `image_hosts`; this is configuration behavior under the existing safety policy, not a confirmed parser bug. Root owns any backed-up, exact-host configuration update.
- Verify in memory that the current NFO/projection returns the upstream-backed title, overview, date/year, actors, and an approved cover URL. Distinguish URL eligibility from MoviePilot host download or disk persistence; do not perform file operations.
- Keep the existing manual search results for both exact native IDs, source/ID uniqueness checks, duplicate handling, and no-shared-number-cache behavior. Automatic recognition stays unique-only; unknown category semantics remain unknown.
- Do not map `video_duration`, `tags`, `imdb_score`, or `rating` until units and meaning are established. Do not synthesize fields or infer that an unobserved director field is universally absent.
- Keep `image_hosts` URL validation/SSRF protections, discovery behavior, unrelated providers, and version `0.3` unchanged. Two search rows alone do not satisfy metadata/artwork acceptance.
- Validate the configuration-only change through the installed runtime: the same native API probe before/after, a bounded anonymous JPEG request, pure in-memory projection/NFO using an existing cached packet, and user confirmation of the cover. Media-file persistence remains untested; no new product code or tests are required.

## Comments

- User/Root report: deployed plugin `0.3` manual search returns native IDs `69a07400131836087e481b7a` and `69a3697d1b1225b605c52f5f`. Live diagnosis found the selected records have a valid HTTPS `cover_image` and the deployment's `image_hosts` is empty, so the existing exact-host policy filters the image. Root owns the backed-up local configuration change; this task does not approve any host by default.
- The inspected payloads contain non-empty overview/date/year and two actors. Upstream also returns `video_duration`, `tags`, `imdb_score`, and `rating`, but the current projection does not map them and their semantics remain unverified. The observed payload did not show a director field. Different `category_id` values remain unexplained. These are incident observations, not universal upstream guarantees.

## Answer

Resolved as a runtime configuration issue. After a verified configuration backup, Root added only `image.healertanker.com` to the empty image-host allowlist. The native API still returns both original IDs, now with poster and backdrop URLs; all other settings, source identity, code and ledger stayed unchanged. The user refreshed MoviePilot and confirmed that the cover is displayed.

The real cached packet produced an in-memory NFO with seven non-empty main fields and two actor nodes. The cover request returned HTTP 200 and a valid 800x538 JPEG. Title/overview/date/year/actors are available; duration/tags/score fields remain unmapped and their semantics are not fully verified. No media transfer, NFO file save or image file save was performed.

Only documentation changed in this repository. Product code, tests and version remain unchanged at `0.3`; the prior 1355-test suite was not rerun. Runtime evidence is retained in the workspace under `outputs/manko-artwork-20261021/` and `records/manko-artwork-metadata-2026-10-21.md`.
