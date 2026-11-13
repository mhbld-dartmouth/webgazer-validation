# Administrator portal language update

This patch changes only `backend/admin_dashboard.html`. No API endpoints, storage, deletion authorization, retention timing, or export logic were changed.

## What an incomplete attempt means
A study can upload gaze data as small pieces before the final confirmation. An interrupted attempt may leave these pieces in Cloud Storage. Such attempts are not listed or exported as completed submissions. After at least 96 hours without activity, researchers may manually review and remove them using the existing protected cleanup endpoint. Completed records and records needing review are excluded from this cleanup.

## What all saved sessions means
The second section is a storage inventory of completed, incomplete, and review-needed sessions. It exists primarily for intentional, password-verified deletion of a specific session.

## No behavior changes
- The cleanup is optional, manual, and uses the same 96-hour threshold.
- Session deletion still requires an authenticated researcher, the full internal ID, and a fresh password.
- Completed session exports and submission logic remain unchanged.

## Deployment
From the existing local GitHub clone, unzip this patch at the repository root, inspect `git diff -- backend/admin_dashboard.html`, commit, and push. Existing Cloud Build deployment should update the page after the next successful build.
