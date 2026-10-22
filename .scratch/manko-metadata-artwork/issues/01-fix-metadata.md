Status: resolved
Type: task

# Map Manko metadata and select usable backdrop artwork

## Problem

The plugin previously mapped the cover to both poster and backdrop, omitted runtime from the media projection/NFO, and had no bounded check to distinguish portrait thumbnails from usable horizontal backgrounds. Manko does not provide the missing director or studio in the observed record. For exact ADN product numbers, the fixed ATTACKERS official detail page can supplement labeled director/runtime fields and an explicitly verified maker identity.

## Required behavior

- Project valid positive bounded runtime minutes to MoviePilot metadata and NFO. Preserve the raw provider record.
- Map the allowlisted cover to poster only. In source order, consider at most eight distinct, exact-host-approved HTTPS backdrop candidates that differ from the cover. Probe anonymously with redirects disabled and no cookies; read no more than 256 KiB to parse the image header. Choose the first horizontal image at least 320×180, with sides no larger than 8192 and total pixels no more than 40 million. Omit backdrop/fanart if none qualifies.
- Bound per-instance selection memo to 128 entries; key it by config generation, hosts, source identity, cover, and candidate list. Clear on config reload and return no artwork if the config is disabled/revoked while a probe runs.
- Supplement only exact `ADN-<digits>` identities from the fixed ATTACKERS detail URL. Require one matching catalog row. Read director/runtime from their exact labeled rows, and map ATTACKERS studio only when official maker metadata explicitly proves the marker. Any fetch or parse failure is non-fatal to Manko identity recognition.
- Do not alter source media, existing source NFO, or user files. MoviePilot owns image retrieval and generated target-sidecar writes; this task does not transfer, link, or delete media.

## Validation

- Use synthetic records and synthetic official HTML. Mock the anonymous image-probe HTTP boundary and cover-first, portrait-skip, eligible-landscape, invalid MIME/dimensions, unapproved host, memo/config-revocation, and async thread behavior.
- Run the pure metadata tests, the official parser/provider tests, and the focused plugin/native SDK integration suite. Native integration must confirm distinct poster/background target assets and verify the source media and any pre-existing source NFO remain byte-for-byte unchanged.
- Report test outcomes from actual execution; synthetic tests do not establish live provider availability or successful production image persistence.

## Answer

Implemented and verified in plugin 0.8.2. The isolated full MoviePilot SDK suite passed 1523 tests plus 7 subtests. One existing ADN-765 sample was then rescraped through the native MoviePilot path: the POST returned HTTP 200 with `success=true`; its target NFO contained director 木村浩之, studio アタッカーズ, and official DVD release runtime 120 minutes. The poster was an 800×538 JPEG and the selected background an 800×534 JPEG with a different hash. The official duration is release metadata, not a source-video playback measurement; the source NFO and source media were preserved.

This 0.8.2 operation regenerated target sidecars only. It did not repeat the real hardlink, directory creation, or rename that had been verified for this same sample in 0.8.0. Five source files retained their hash, mtime, and inode; the existing target video remained the same inode as its source, with inode 186975 and `nlink=2`. MoviePilot core, `/public`, config, three ledgers, identity revision 9, and the previous metadata cache stayed unchanged. Automatic processing remains disabled. Other sources and qBittorrent new-download automation have not received real-world acceptance. See the [deployment and sample audit record](../../../../records/manko-metadata-artwork-fix-2026-10-22.md) for the full evidence.

## Comments
