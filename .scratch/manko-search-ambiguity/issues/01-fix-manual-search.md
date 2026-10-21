# Return all exact Manko matches from manual search

Type: task
Status: resolved
Blocked by: none

## Goal

Allow the explicit MoviePilot manual Manko search hook to expose all fully validated, distinct native IDs when one exact number has multiple exact records, without changing automatic recognition's strict unique-match behavior.

## Acceptance criteria

- A shared sync/async regression test uses two exact Manko detail fixtures for `ADN-765` and asserts both native IDs are returned by the public manual search hook.
- Automatic number recognition still abstains on the same ambiguous evidence; manual results do not write an arbitrary `manko:number:` cache entry.
- Each selected native ID can be fetched independently; duplicate IDs are deduplicated and malformed/truncated/mixed-number detail data remains fail-closed.
- Existing source selection, generation guards, transport policy, ledger format, unrelated providers, and automatic discovery behavior remain unchanged.
- Plugin and package version become `0.3`; README describes the manual-versus-automatic matching boundary without claiming NAS acceptance.
- Record the SDK-backed red/green test results and relevant focused/full suite outcomes.

## Comments

- Claimed for implementation on `fix/manko-search-ambiguity`; the initial two-record public-hook regression is prepared for the isolated SDK test environment.
- Root ran the exact two-case candidate in an isolated Python 3.14.7 / MoviePilot V3.1.6 SDK environment with network disabled and no production mounts: `run-isolated-tests.py candidate-red.tar tests/test_query_diagnostics.py -k manual_manko_search_returns_each_exact_ambiguous_native_id` produced 2 expected failures (`[]` instead of the two source/native-ID pairs), 72 deselected. The tested candidate tar SHA256 was `ef080e68ffd3596c086c98e298591af6afae96e19901511d50f428a26ce7e6f0`.
- Root's first full candidate run passed all four new manual multi-ID, explicit-ID recheck, automatic ambiguity, and duplicate-ID cases; 12 remaining failures exposed old test-double/search-cache expectations. Those tests are being updated to match the new manual-vs-recognition contract; final suite rerun is pending.
- Root reran the final candidate as a full suite in the isolated Python 3.14.7 / official MoviePilot V3.1.6 SDK environment: `1355 passed in 41.02s`, with network disabled and no production mounts. The tested `candidate-green2.tar` SHA256 was `2284119e8643e735cf0a9b84b30cc8dc77ee2d36e5497830822d7fb861e33c8e`. This validates the replay suite only; production acceptance remains pending.
- Production acceptance completed for plugin `0.3`: deployment payload reported `payload_deployed`, and the native POST reload returned HTTP 200 at `2026-10-21T12:33:49Z`. At `2026-10-21T12:34:13Z`, the native `GET /media/search` request for title `ADN-765`, type `media`, source `manko` returned HTTP 200 and exactly the expected IDs `69a07400131836087e481b7a` and `69a3697d1b1225b605c52f5f`, both from Manko. Search, both detail requests, and both actor requests returned HTTP 200; diagnostics recorded `accepted` twice. The two exact-number records had different `category_id` values, and the live evidence did not establish which category denotes the intended product/version; the manual search therefore exposes both without inferring category meaning or choosing one. Existing configuration, source identity, ledger, and backups were unchanged; discovery remained disabled and no file task was run. MoviePilot UI behavior remains unverified.

## Answer

The unique-only automatic Manko search returned no result for `ADN-765` because two distinct native IDs both had exact, fully validated details. Version `0.3` now exposes both records only through the explicit native manual search hook; automatic recognition remains unique-only and the multi-result path does not populate a shared number cache. The isolated official MoviePilot V3.1.6 SDK suite passed (`1355 passed in 41.02s`), and production acceptance confirmed the deployed `0.3` native API returns exactly IDs `69a07400131836087e481b7a` and `69a3697d1b1225b605c52f5f`. Their different `category_id` values remain semantically unknown, so neither record is preferred. The plugin UI remains unverified.
