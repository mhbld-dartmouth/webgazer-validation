# Full study flow cleanup and separate timing exports

Updated November 13, 2026. Supersedes the limitations listed under the *Still to be implemented* heading in `CHANGELOG_STEP1_SESSION_UTC.md`. That older changelog documents only the first incremental release.

## Participant workflow

* Server generated anonymous 12 digit session number instead of assigned ID. The secure internal ID remains separate and is not entered by participants.
* No additional consent form, typed name, or consent record verification is required at final submission. The initial privacy acknowledgment is retained as the enrollment step.
* Fullscreen is required to start calibration or E search and to continue active tasks. Browser permission still requires a participant click. Loss of fullscreen triggers a task pause.
* Required positioning chime, fixed basic tracking settings, no advanced participant settings, no gaze overlay, no participant download buttons.
* The cursor is hidden for calibration and validation, with bottom time/progress indicators hidden. It appears for selections and E search clicks.
* Exactly 12 calibration targets, then 50 validation presentations (25 by 2). If screening fails, automatic recalibration and a second validation. There are no more than two calibration attempts, no participant visible pass/fail, no manual retry choices.
* Sequence continues to 10 practice E search trials and 50 recorded E search trials, then one automatic upload and server confirmation screen. No intermediate download or manual finish/submit screens.
* E layouts allocate 18 objects over four quadrants with randomized offsets that respect stimulus boundaries, with target positions randomized among those objects. Browser geometry tests check for nonoverlap.
* Additional relative head and face geometry monitoring using observed eye separation, eye center position, head turning ratio and the existing distance estimates. A change from calibration pauses the task with an audible warning; real camera validation is still necessary to evaluate sensitivity/specificity.

## Sampling and timestamps

* Raw validation gaze intervals, per target statistics and the valid WebGazer effective sampling rate (Hz) are retained **separately** from camera frame timing.
* Camera reported track FPS and observed HTML video frame callback FPS are separate; they are never represented as direct sensor FPS.
* New researcher CSVs: `sampling_summary.csv`, `validation_timing.csv`, `validation_intervals.csv`, `camera_intervals.csv`, `validation_runs.csv`.
* All validation attempts remain in the export with run numbering. Repeated final checkpoints do not duplicate validation points.
* Backend records UTC time of accepted completion as `submitted_at_utc`. The same timestamp appears in the confirmation receipt and research exports, including retries.

## Verified locally versus outstanding

The FastAPI API tests, upload and gaze utility tests, and simulated Chromium full sequence and 600 layouts have been run locally. Simulated tests confirm one automatic submission after the E search. Actual webcam face geometry and observed camera FPS, physical head movement detection, accuracy, audio audibility, and full deployment to the study server **have not** been validated and need a small pilot before recruitment.

A session's collected data remain only in volatile page memory before final submission; closing or refreshing the tab before server acknowledgment can lose them. Losing the network at submission leaves the page with a Retry button and no participant download alternative. This is an intentional workflow choice with a data retention tradeoff.
