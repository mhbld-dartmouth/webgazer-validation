# Gaze Check: webcam gaze validation study

Gaze Check is a desktop/laptop browser study that uses WebGazer 2.0.1 to measure the performance of webcam based gaze predictions. It includes camera setup, repeated calibration and validation, and an E search task. It is a research pilot, not a clinical eye tracker or a hardware validation against a reference system.

## Participant procedure

1. Read the short privacy notice, acknowledge participation, and answer setup and optional demographic questions. No assigned study ID, name, or manually entered session number is needed. The server generates a 12 digit random session number and a separate secure internal identifier.
2. Check webcam access, face visibility and lighting, screen measurements, and viewing distance. The app asks for a tape measure when available, but a participant can continue without one. The app asks the participant to close all other apps.
3. Enter browser fullscreen using the displayed start button. Fullscreen is required for calibration, validation and E search. Browsers require a real user interaction to enter fullscreen, so the application cannot silently override that permission. Returning to a window pauses the task until fullscreen is restored.
4. Complete 12 calibration targets in a 4 by 3 grid, followed by **50 independent validation presentations**: 25 distinct targets in two randomized passes. Validation uses 2,000 ms per target and a 250 ms blank interval. The first 100 ms of each presentation are excluded from quality and gaze timing calculations.
5. If the first validation does not meet the predeclared gaze quality and validity rules, the app automatically calibrates once more and repeats the full validation. **Two calibration attempts maximum**; participants do not see quality grades or choose a recalibration. Regardless of the second result, the study proceeds to search.
6. Perform 10 practice E search trials followed by 50 study E search trials. Each trial displays 18 separated stimuli distributed across four screen quadrants, with randomized locations inside the quadrants and exactly two target Es. A layout is rejected if items would overlap. The E search requires mouse selection, so a pointer remains available there; it is hidden during dot calibration and validation. Intermediary progress text/bars and participant results screens are hidden.
7. After all 50 study E search trials, the app automatically prepares its analysis, uploads study records, and shows the server receipt. There is no manual final finish/submit decision, gaze visualization, recalibration button, or participant data download. A Retry submission button appears only if the automatic network operation fails.

The opening privacy acknowledgment is still required **at study entry**, but there is no consent recheck or typed signature at submission. Sites must ensure that the study entry acknowledgment is adequate under the applicable research ethics approval before recruiting participants.

During active tasks, loss of the tracked face, estimated head displacement from the calibrated position, estimated distance change, or loss of fullscreen pauses the task. The positioning chime is always enabled in the code and repeats during pauses. Browser audio policies, muted computers, and uncalibrated webcam pose estimates mean that audible warnings and movement detection cannot be guaranteed for all participants.

## Gaze sampling versus camera frame rate

These are **three distinct measurements** saved per validation attempt:

| Export column | Meaning | Source |
| --- | --- | --- |
| `effective_sampling_hz` | Mean rate of retained valid WebGazer gaze predictions | `1000 / inter_sample_mean_ms` |
| `observed_camera_fps` | Video frames delivered to the browser's video element per second, estimated from consecutive observed frame presentation times | `HTMLVideoElement.requestVideoFrameCallback()` (when supported) |
| `camera_reported_frame_rate_fps` | Frame rate reported in the active camera track configuration | `MediaStreamTrack.getSettings().frameRate` (if available) |

**How gaze interval timing works.** At every gaze prediction, the app calls `performance.now()`. It retains finite in-bounds gaze predictions inside the analyzed portion of each validation target (after the first 100 ms). For consecutive retained predictions **within that target only**, it calculates the difference in milliseconds; it does not bridge between targets or blank intervals. The app then pools these per-target intervals across the validation run. The average and population standard deviation are stored. Rates are `1000 / mean_interval_ms` if at least three measured intervals are available. Gaps from missing or invalid predictions can reduce this effective valid prediction rate. The mean covers all validation targets, including those subsequently marked unusable or interrupted. Each interval also has the relevant target number and relative session timestamp saved.

**How camera timing works.** When browser support exists, `requestVideoFrameCallback()` supplies the presentation time for a video frame. Differences between consecutive frame presentation timestamps are retained during validation, and the average interval gives the observed delivered video FPS. This is not an independent camera sensor FPS measurement. Frames can be dropped before the browser callback. If the API is unsupported or insufficient data exist, `observed_camera_fps` remains blank rather than being guessed. The reported camera track setting is saved separately; it is not substituted for the observed value.

### Researcher export files

The password protected dashboard continues to allow researchers to download a single session or the study as ZIP archives. Each session includes raw compressed JSON plus these CSVs:

* `sampling_summary.csv`: one row **per validation run**; separate camera FPS, valid gaze Hz, interval means and SD, sample counts, methods and UTC receipt.
* `validation_intervals.csv`: all consecutive valid WebGazer callback intervals and their two relative session timestamps, separated by target and validation run.
* `validation_timing.csv`: per-target gaze timing summary, counts, usability and interruption flags.
* `camera_intervals.csv`: observed video frame intervals and relative session timestamps, separated by validation run.
* `validation_runs.csv`: validation attempts and whether they met quality requirements.
* `validation_points.csv`, `gaze_events.csv`, `search_trials.csv`, `practice_trials.csv`, `other_fields.csv`, plus other analysis records and plots.

The server records `submitted_at_utc` at successful completion, using the **server clock**. This ISO 8601 UTC timestamp is included in the acknowledgment, completed session, dashboard and exports. A repeated completion call preserves the original receipt time. Relative performance timestamps are measured on the participant's own machine and are not synchronized with the server clock.

## Deployment and requirements

The FastAPI backend, static participant site and researcher dashboard can run together with the included `Dockerfile` on Railway or an equivalent container host. The backend needs a private S3 compatible bucket and environment credentials. See `RAILWAY_DEPLOY_V2_6.md` for earlier platform configuration guidance and confirm environment variable names against `backend/main.py` before deployment. Run the new build on a staging site before replacing the current research URL. The deployment file `deploy-config.js` must point to the appropriate HTTPS API. Face detection requires access to model files, which may be restricted by browser/network configurations.

Researcher exports must remain protected, and collected gaze, response, browser and optional demographic information must be handled under the approved research security plan. No participant recordings, credentials, or study data are distributed in this source bundle. Camera images are processed locally; no face images or video are intentionally saved or uploaded by the application.

### Tests

Run from the project directory:

```bash
python -m pytest backend/tests/test_api.py -q
node test_quality.cjs
node test_kinematics.cjs
node test_upload_node.cjs
python browser_full_flow_smoke.py
```

The browser test requires Playwright and Chromium; it uses **mock WebGazer and simulated face input**, not actual camera hardware. It checks 600 E layouts across four common desktop viewports and runs the full pipeline. Real webcam, different browsers and hardware, audio permission/muting, head tracking reliability, slower devices, and deployed server storage/upload remain **to be tested with pilot participants**. No guarantee of positional gaze accuracy or stable camera FPS is implied by the unit tests.

## Project files

| File | Responsibility |
| --- | --- |
| `index.html` | Setup, fullscreen task control, face monitoring, calibration, validation, E search and automatic submission |
| `quality.js` | Gaze validity/quality calculations |
| `gaze_kinematics.js` | Descriptive fixation and saccade candidate analysis |
| `upload.js` | In-memory checkpoints and HTTPS submission |
| `backend/main.py` | Public session API and secure storage |
| `backend/export_reports.py` | Researcher CSVs, raw JSON and plots |
| `backend/admin_portal.py` | Protected researcher dashboard API |
| `backend/admin_dashboard.html` | Researcher interface |
| `browser_full_flow_smoke.py` | Simulated browser full study and geometry checks |
| `CHANGELOG_FULL_PIPELINE.md` | Changes in this bundle and verification limits |

## License and acknowledgments

The source code in this project is supplied under GPLv3 (`LICENSE`). The included unmodified WebGazer library and bundled dependencies retain their original notices; see `THIRD_PARTY_NOTICES.md`.

The study design was informed by Brand et al. (2021) and WebGazer by Papoutsaki et al. (2016). The browser procedure differs from the Gazepoint GP3 hardware study and should not be treated as equivalent.

*Repository maintained by MHBLD Dartmouth*
