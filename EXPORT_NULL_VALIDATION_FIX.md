# Export repair for completed sessions with null validation checkpoints

## Problem reproduced

The production session `54975728c5f42133a036eb843a397b79` has 124 uploaded batches and a valid completion receipt. Intermediate streaming batches contain `validation_summary: null` before the final summary is available.

The export pipeline's accuracy plot loop did `json.loads('null').get('results')`, which raises `AttributeError: 'NoneType' object has no attribute 'get'`. Its final summary loop had the same assumption. The earlier recovery version catches the exception and exports only raw JSON records.

## Fix

In `backend/export_reports.py`, both loops now explicitly skip a non-dictionary validation summary. This allows the final valid summary to populate accuracy and precision and produce plots. No changes to participant uploads, storage objects, receipt status or deletion controls.

## Verification

* 14 backend tests passed, including a new regression test that uploads an intermediate null checkpoint followed by a final validation summary, then exercises both individual and all-sessions exports.
* An isolated offline replay from the user-provided raw recovery ZIP produced the session's processed CSVs and plots, with 50 E search study trials, 10 practice trials, and 13,880 gaze records.
* Actual Cloud Run deployment and the second historic session remain to be verified. This repair does not guarantee the other historic session is fully readable.

## Deploy

From the existing local Git repository, extract the fix to the repository root, run the backend tests, commit only `backend/export_reports.py`, `backend/tests/test_api.py` and this document, then push to `main`. Verify Cloud Build and Cloud Run update before downloading a fresh all sessions ZIP.

Do not delete stored sessions until they have been successfully exported and checked.
