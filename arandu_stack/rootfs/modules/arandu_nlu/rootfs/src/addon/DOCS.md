# ARANDU NLU

Release 0.3.2. This is the sole published add-on source; slug `ptbr_nlu`
and repository identity are unchanged. Refresh the store, update this app,
wait for its source image build, then restart it. Update the matching
`custom_components/local_nlu` integration manually and restart Home Assistant.
Updating the app does not install or update that integration. Preserve options
and take a HA backup first. See the repository's INSTALL.md for migration,
contextual verification, package checksums and rollback.

Install and start this app, then install the `local_nlu` companion integration
and enter the app's internal HTTP origin. No app options are required.

Endpoint by install route (Supervisor DNS uses `{REPO}_{SLUG}` with `_`
mapped to `-`): local installs use `http://local-ptbr-nlu:11555`; installs
from a GitHub store use `http://<8hex>-ptbr-nlu:11555`, where `<8hex>` is the
first 8 hex digits of SHA-1 over the lowercased store repository URL
(confirm it in Supervisor → Add-ons or via the Supervisor API `/addons`
endpoint). The integration validates locality, performs a live
`GET /health` check, and refuses anything else, so a wrong hostname fails
at setup time, never silently at runtime.

The app interprets request-local text and catalog data in memory and returns a
typed plan. It cannot call Home Assistant. The companion integration performs
all live checks and is the only component that can execute services.
