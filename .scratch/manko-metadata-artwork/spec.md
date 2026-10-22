# Manko metadata and artwork projection

## Scope

Project verified Manko metadata into MoviePilot media fields and NFO, supplement only missing metadata for exact ADN numbers from the fixed ATTACKERS official detail page, and select a usable background from approved artwork URLs. Synthetic fixtures and mocked HTTP boundaries cover these behaviors. MoviePilot remains responsible for downloading images and writing NFO; this task never transfers, links, modifies, or deletes source media.

## Confirmed data and mapping

- Manko provides a positive integer `video_duration` in minutes, a `cover_image`, and a `backdrop_images` list of URL strings.
- The normalized internal projection accepts `directors` and `production_companies` as lists of `{name: string}`. The official supplemental parser extracts director and runtime from labeled ATTACKERS detail rows, and emits `production_companies` only when the fixed official page has the ATTACKERS maker marker.
- The documented official sample distinguishes source NFO runtime 118 from ATTACKERS release runtime 120; preserve existing source NFO and only use official runtime to fill a missing metadata value.

## Acceptance criteria

- Project a valid positive bounded Manko runtime to MoviePilot `runtime` and `<runtime>` in NFO; reject malformed values and never mutate the provider record.
- Map the approved cover to `poster.jpg` and `poster_path`. Background candidates must be HTTPS URLs on an exact configured host, differ from the cover, and retain source order. Probe at most eight candidates anonymously, with redirects disabled, no cookies, and at most 256 KiB read for the image header. Select the first decodable horizontal image at least 320×180, with each side at most 8192 pixels and at most 40 million pixels total. If none qualifies, omit fanart/backdrop rather than reusing the cover.
- Memoize no more than 128 artwork results per plugin instance, keyed by configuration generation, approved hosts, source identity, cover, and candidate list. Clear memo on config reload and suppress results if enabled state, generation, or hosts change during probing.
- For exact `ADN-<digits>` only, fetch a fixed `https://attackers.net/works/detail/ADN<digits>` path. Require one exact catalog row match; read only the labeled director and runtime rows. Add the ATTACKERS company only when official maker metadata proves the marker. Never guess fields from titles, plots, actors, recommendations, or other domains. Fetch/parse failure leaves primary Manko recognition intact.
- Preserve raw provider records and existing source files. Image writes remain on the MoviePilot-owned path; no source transfer, hard link, file deletion, or source NFO overwrite is part of this task.

## Evidence boundary

Unit and SDK tests use synthetic metadata/HTML and mocked HTTP boundaries. Separately, one existing ADN-765 sample completed a real native MoviePilot rescrape: the NFO and target JPEG poster/background were verified as valid and distinct. The official 120 minutes is DVD release metadata, not a measurement of the source video's playback duration. This 0.8.2 pass only regenerated target sidecars; it did not repeat the hardlink, directory creation, or rename verified for the same sample in 0.8.0. Five source files retained their hash, mtime, and inode; the target video remained the same inode as its source with `nlink=2`. These results do not establish other-title/other-source coverage or qBittorrent new-download automation. See the [deployment and sample audit record](../../../records/manko-metadata-artwork-fix-2026-10-22.md) for full evidence and system-state checks.

## Verification result

- Full isolated MoviePilot SDK suite: 1523 passed and 7 subtests passed.
- Native rescrape: HTTP 200 with `success=true`; NFO contained director 木村浩之, studio アタッカーズ, and 120-minute official DVD duration.
- Target poster JPEG: 800×538. Target background JPEG: 800×534. Their hashes differed.
- Source preservation: the five source-file hashes, mtimes, and inodes remained unchanged; the target video remained inode 186975 with `nlink=2`, the same inode as the existing source hardlink.
- During this rescrape, MoviePilot core (6 files), `/public` (1167 files), plugin config, all three ledgers, source-identity revision 9, and the previous metadata cache remained unchanged; automatic processing remained disabled.
