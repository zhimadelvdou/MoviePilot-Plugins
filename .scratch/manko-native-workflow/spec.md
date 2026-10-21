# Manko native recognition-to-transfer identity

## User goal

From MoviePilot file management, automatically extract the number from the selected folder/file name under the chosen Manko source, keep the original downloaded file, hard-link the selected media into a different user-confirmed destination, and let MoviePilot create the destination directory/name and write NFO and artwork there. Daily manual entry of a native media ID is not an acceptable final workflow.

## Baseline evidence and remaining acceptance

On 2026-10-21, the deployed `XiaojiejieScraping` 0.3 returned two exact Manko candidates for the tested number. Automatic recognition correctly failed closed with `ambiguous_match` and returned no media context. A path-only file-manager `recognize_file` request returned HTTP 200 with `context.media_info=None`; an explicit Manko request for the same path also returned an empty media context after the two-candidate ambiguity. A separate lookup by a known native ID returned usable Manko detail metadata. These results do not prove the file-manager flow carries recognition identity into reorganization.

The path-only file-manager recognition GET and an explicit Manko recognition request were separately observed; see the workspace incident record for their results. Source override fields and the complete source/ID handoff remain under verification. Static inspection of the V3.1.8 frontend bundle shows that movie type reveals the native-ID search row. On 2026-10-22, the user confirmed this control appears in the live UI: an empty-ID preview failed with “no media information”; after manually entering known ID `69a07400131836087e481b7a`, preview succeeded for Manko `ADN-765 (2026)`. This proves preview can use a manually supplied ID, not the requested automatic folder/name lookup or a completed transfer. The user explicitly rejects daily manual ID entry.

For V3.1.6, a video recognition request without an explicit source defaults to TMDB. The plugin may receive a broadcast recognition call, but when TMDB is explicitly supplied the Manko plugin exits with `source_unselected` without querying Manko. Backend `manual_transfer` requires `media_source` and native `media_id` to be supplied as a pair when either is present. The detailed host-workspace incident record is `records/manko-native-recognition-2026-10-21.md` outside this repository.

## Required behavior

- The file-manager flow must extract a number from the selected folder/file name and query using the chosen Manko source without daily manual ID entry.
- When multiple exact Manko IDs match a number, automatically choose a stable representative only if the detail records differ solely in the explicitly observed transport/category fields, all required title/date/cover/overview metadata is present, and each work's named-actor response set matches exactly. Any other raw-field, actor, or completeness difference remains ambiguous; never choose the first result silently or poison the shared number cache. Manual search continues to expose every exact ID.
- Carry the resolved `(media_source, media_id)` identity consistently through preview and final transfer. Automatic preview is verified; final real transfer and file persistence remain unexecuted.
- Reject missing, mismatched, or changed identity between preview and transfer. Do not fall back to TMDB while presenting the result as Manko.
- Preserve the downloaded original and use a hard link only after the user confirms the exact destination and operation. The observed auto-matched destination `/media/Link/电影/` is not yet confirmed as the final target.
- Keep automatic discovery disabled and preserve existing source/ID validation, path safety, ledger, and host safety boundaries.

## Acceptance

1. The file-manager automatically extracts the number from the selected folder/file name and queries the selected Manko source; the daily workflow does not require manually entering a native ID.
2. For a unique exact native ID, the workflow resolves it automatically. For multiple candidates, it does not silently choose the first; any required disambiguation is explicit and safe.
3. The preview and final transfer both contain the same non-empty `(media_source, media_id)` pair. A missing or changed pair stops the operation.
4. With an isolated test source and a user-confirmed destination, the target and source share device/inode, the source remains present, and its hash is unchanged.
5. MoviePilot creates the expected target directory/name for the selected identity and actually writes the expected NFO and cover image into the target. In-memory metadata and image URLs alone do not pass this criterion.
6. Automatic discovery remains disabled; no production transfer or scrape is required for code-level tests.

Current status: the automatic filename/folder recognition fix is implemented and deployed. Version 0.4 resolves strictly equivalent Manko records with a stable representative; other ambiguity remains fail-closed and manual search still exposes every ID. The frontend sends independent `recognize_source` when the ID is blank; the backend forwards and persists it per request without weakening identity-pair validation. On 2026-10-22 the live no-ID preview returned one success and zero failures for the user directory, with the expected target path. Plugin tests passed 1397 cases, frontend dialog 61 cases plus types/lint/build, and related backend tests passed 114 cases. Config, source binding, ledger and original directory entries were preserved. The broader workflow task remains open for actual hard-link inode/source-content verification and destination NFO/artwork persistence; no real transfer or scrape was executed.

Actor-response evidence boundary: the current actor endpoint is queried by work ID. In the observed response, it returned two actors with only a top-level `data` list and no `total`; requests with `size=1,page=1` and `size=1,page=2` returned the same two-item set. The equivalence check treats each work's returned set as its actor metadata and compares those sets. It does not claim that the upstream actor catalog is complete. A response with `total` must match the returned length, and an unbounded full page of 24 without `total` is rejected.
