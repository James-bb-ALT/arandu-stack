# Changelog

## 0.4.5

- Execute collective room/category actions on all available authorized devices, skipping offline members from the initial snapshot.
- Reject empty target sets and plans whose availability, permissions or membership change before execution.
- Report a missing named device using the understood target, preserving request-local Unicode without logging the utterance.
- Update the add-on and Python integration together; v1–v3 remain compatible.

## 0.4.0

- Add contextual name/room set resolution, safe collective area shutdown and bounded dialogue choices.
- Add metric queries with unit validation, informative global/local lists and configurable low battery threshold.
- Associate personal devices and sensors using Home Assistant metadata and explicit bindings.
- Consume dynamic HA location observations with mandatory provider timestamps; never infer physical location from registered area.
- Add private-data-free version/protocol diagnostics and specific spoken failure reasons.
- Keep v1–v3 and `/health` compatible. Update the Python integration separately from the add-on.

## 0.3.2

- Publish only `addon/`; remove the stale duplicate store app.
- Preserve the public `ptbr_nlu` slug and repository identity.
- Synchronize Rust, add-on and integration versions; validate store structure
  and distribution changes in the standard gate.
- Include runtime license resources and provide separate reproducible ZIPs.
- Document store refresh, source image rebuild, manual integration update and rollback.

## 0.3.1

- Set the contextual implementation's release version to 0.3.1.
  The obsolete duplicate still advertised 0.2.0 until 0.3.2.
