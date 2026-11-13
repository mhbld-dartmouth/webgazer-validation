# Cloud Run browser version and caching fix

Why: An already open participant or researcher tab keeps running its previously loaded JavaScript even after Cloud Build deploys new code. Previous browser reloads with a different URL worked but required administrators to know cache-busting tricks.

## Changes

- HTML, JS assets, face model endpoints, and private API responses explicitly return `Cache-Control: no-store, max-age=0, must-revalidate`, `Pragma: no-cache`, and `Expires: 0`.
- The HTML embeds the serving Cloud Run revision (`K_REVISION`). With local runs, this is a digest of study and portal source files.
- Participant script URLs include that revision as a query parameter so the browser always requests the assets corresponding to its HTML.
- A same-origin, nonpersonal `/api/client-version` endpoint reports the current revision; it is never cached.
- Participant setup detects if a new deployment is present and asks for a refresh before creating a new session. Once a study session has started, the version guard stops checking. It never automatically reloads calibration, the E task, or submission.
- Administrator portal shows a manual refresh notice when outdated. It never interrupts an export or deletion. Researcher login is in page memory, so refreshing requires signing in again.
- No browser cache, local storage, completed records, uploaded batches, or database records are deleted by this change.

Existing already-open tabs from before this fix will need **one** manual reload to receive the new version checker. Once this patch is deployed, future updates are detected without URL tricks.

## Verify after the Cloud Build deployment

```bash
curl -sS -D - -o /dev/null 'https://webgazer-validation-229179610277.us-central1.run.app/' | grep -iE 'cache-control|pragma|expires'
curl -sS -D - -o /dev/null 'https://webgazer-validation-229179610277.us-central1.run.app/admin' | grep -iE 'cache-control|pragma|expires'
curl -fsS 'https://webgazer-validation-229179610277.us-central1.run.app/api/client-version'
```

Open the participant site and administrator site after deployment. Leave the administrator page open during a future deployment to see the refresh message. Do not deploy deliberately in the middle of participant testing if avoidable.

Security: no unauthenticated raw study records are exposed by the new endpoint. Cache management never overrides existing authorization checks.

## Install in existing GitHub checkout

Download the ZIP into Downloads, then run:

```bash
cd ~/Desktop/webgazer-validation
unzip -o ~/Downloads/webgazer-cache-version-fix.zip -d .
git diff --check
git status --short
git add index.html backend/main.py backend/admin_portal.py backend/admin_dashboard.html backend/cache_version.py backend/tests/test_api.py CACHE_DEPLOYMENT_FIX.md browser_cache_version_smoke.py
git commit -m "Prevent stale WebGazer participant and admin versions"
git push origin main
```

Wait for Cloud Build to deploy to the existing Cloud Run service. Check `/api/client-version` and ordinary participant/admin URLs. One manual refresh of already-open tabs created before this fix is still necessary.
