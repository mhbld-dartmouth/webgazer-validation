# Session number and UTC submission update

Updated November 13, 2026.

## Implemented in this version

1. Removed the assigned participant ID entry. A randomly generated 12 digit session number is assigned on study start. It is used as the participant label throughout the study output. The separate 128 bit technical session ID continues to secure and organize stored records.
2. The numeric session number is included in upload records, session metadata, participant exports, researcher dashboard, and the study summary CSV.
3. The server now records `submitted_at_utc` at successful submission completion using its own clock, in UTC ISO 8601 with milliseconds. It is included in the final JSON receipt, completed session object, researcher dashboard, and summary CSV. Retrying completion preserves the original timestamp.
4. Fixed an existing mismatch where the upload code required a typed name that the participant form did not collect. A study acknowledgment can still be recorded at the start, but it is not required again to finalize a submission.

## Still to be implemented in later steps

The participant can still choose the final submit button. Fullscreen, forced chimes, cursor hiding, task display cleanup, automatic calibration retry flow, gaze toggle removal, download control removal, search display randomization, head movement checks, and automatic final submission have **not** been changed in this package.

## Test results

Backend API tests, upload module tests, JavaScript syntax check, and an isolated Chromium UI smoke test pass locally. These are simulated and unit tests. Camera tracking, research storage, and full live study flow have not been validated against the deployed server.
