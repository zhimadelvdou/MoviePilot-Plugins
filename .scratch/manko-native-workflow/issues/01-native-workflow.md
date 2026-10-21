# Carry Manko identity from file recognition into transfer

Type: task
Status: claimed
Blocked by: none

## Goal

Make MoviePilot file management automatically extract the number from the selected folder/file name under the chosen Manko source, resolve the native identity without daily manual ID entry, and carry it into transfer preview and operation while preserving the original and meeting the hard-link, directory/name, NFO, and artwork requirements.

## Acceptance criteria

- Automatically extract and query the selected folder/file number under the chosen Manko source; daily manual native-ID entry is not acceptable.
- Resolve a unique exact Manko ID automatically. If multiple exact IDs remain, never silently choose the first; use a safe explicit candidate path only as needed.
- The same non-empty `(media_source, media_id)` pair is present in preview and final transfer. Missing or changed identity fails closed; do not disguise default TMDB as Manko.
- After user confirmation of destination and hard-link operation, an isolated validation proves same-device inode identity, source persistence, and unchanged source hash.
- MoviePilot creates the expected destination directory/name and writes NFO plus cover image to the target. In-memory output or a non-empty URL is insufficient.
- Automatic discovery remains disabled.

## Current evidence

- At about 2026-10-21 23:50 +08:00, the path-only recognition GET for `/media/Qbit/AV/ADN-765-C/` returned HTTP 200 with `context.media_info=None` (439 ms). The valid evidence is `recognition-loop2.jsonl`; `recognition-red.jsonl` was only a probe contract mismatch and is not a RED result.
- Explicit Manko recognition for the same path returned HTTP 200 but no media context. Diagnostics show two exact candidates, two successful details, `ambiguous_match` count 2, and `empty_result`.
- An explicit lookup of known native ID `69a07400131836087e481b7a` returned HTTP 200 and usable Manko identity and metadata. The read-only requests left the ledger logical SHA unchanged.
- The deployed 0.3 Python source hashes matched. Backend static version is 3.1.6; `/public/version.txt` reports frontend 3.1.8. No transfer, scrape, or media-file operation was performed.
- Static inspection of the V3.1.8 bundle shows the movie-type flow exposes a media-ID search row. On 2026-10-22, the user confirmed it appears live: empty ID preview failed; manually entering `69a07400131836087e481b7a` produced a successful Manko `ADN-765 (2026)` preview. This confirms the UI can preview a manually supplied ID, not automatic folder/name lookup or a completed transfer. The user rejects daily manual ID entry.
- The path-only recognition GET and explicit Manko recognition produced separate diagnostic results. Complete source override and identity handoff remain under verification; do not infer from the empty-ID screenshot that no Manko source was passed.
- The second preview screenshot showed a hard-link target under `/media/Link/电影/外语电影/ADN-765 (2026)/`, category classification on, type classification off, and metadata scraping on. This is preview evidence only, not proof of transfer or file persistence.

See [the feature specification](../spec.md). The detailed host-workspace incident record is `records/manko-native-recognition-2026-10-21.md` outside this repository.

## Comments

2026-10-22 deployment validation: plugin 0.4 plus the frontend/backend request-source fix are installed on DSM920. The live preview with `recognize_source=manko` and no media ID produced one successful target and no failures. Automatic path recognition returned Manko native ID `69a07400131836087e481b7a`; title, overview, dates, actors and artwork fields were present. Plugin regression: 1397 passed; frontend dialog: 61 passed plus typecheck/lint/build; backend related: 114 passed. A 975-file pre-deployment backup was hash-verified. Configuration and ledger remained unchanged; source metadata only advanced its revision. Actual transfer, inode/content verification and destination NFO/artwork writes remain unexecuted, so this broader issue stays claimed. Host record contains deployment hashes and rollback artifacts.

2026-10-22 implementation checkpoint (before runtime validation): Version 0.4 implements a narrow automatic resolution path for multiple exact Manko IDs. It selects the lexicographically smallest native ID only after all exact detail records are present, required title/date/cover/overview metadata is complete, raw records match except for the explicitly observed category/transport fields, and each work's returned named-actor set matches exactly. The current actor endpoint is keyed by work ID; observed `size/page` variations returned the same two-item set without `total`, so this compares the returned set and does not claim a complete upstream actor catalog. Unknown or unmapped raw-field differences, metadata gaps, failed/incomplete actor responses, and different actors remain ambiguous. Manual search still returns every exact ID. Offline regression coverage was added for both sync and async paths; real-SDK pytest validation is pending. The broader file-manager source/identity handoff, transfer, and file-persistence acceptance criteria remain open, so this issue stays claimed.
