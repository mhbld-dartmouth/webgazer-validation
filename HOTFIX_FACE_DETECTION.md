# Face detection hotfix, 2026-11-13

## Bug fixed

The `onFaceFrame` callback declared `wL` and `wR` inside an `if (ipd > 3)` block, then used them outside that block while assigning `faceInfo.turn`. With ordinary face landmarks, this throws `ReferenceError: wL is not defined`, which interrupts the WebGazer `getEyePatches` callback and prevents the initial camera check from stabilizing.

The fix calculates the two eye widths and turn ratio before the conditional, then reuses that ratio both for tracking history and `faceInfo`.

## Robustness improvements

* Increased the allowable gap between valid face-tracking updates from 500 ms to 2500 ms. The task still requires actual facial landmarks; merely seeing a webcam video frame cannot satisfy the face check.
* Increased the lost-face watchdog interval from 1500 ms to 3500 ms to accommodate intermittent, slower inference.
* Counted tracking calls, results, usable landmark frames and exceptions for more specific camera-check guidance.
* Rejects stale face landmarks from a previous frame when WebGazer did not detect a new face.

## Local verification

* `python browser_face_detection_smoke.py`: synthetic FaceMesh landmarks now reach `faceInfo`; the previous `wL` error would fail this test.
* `python browser_full_flow_smoke.py`: automated study sequence and randomized search layouts passed with simulated gaze.
* `python -m pytest backend/tests/test_api.py -q`: seven backend tests passed.
* All inline JavaScript parsed with `node --check`.

These tests **do not** confirm that the model detects faces in a real webcam on the participant's hardware. Retest on the affected laptop in Chrome, preferably with browser console open, before reopening recruitment. Existing Cloud Run secrets and backend files require no changes for this hotfix.
