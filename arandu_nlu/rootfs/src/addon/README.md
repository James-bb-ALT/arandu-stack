# ARANDU NLU app

This Home Assistant app exposes only a passive internal HTTP interpretation
service on TCP port 11555. It has no Home Assistant API access, credentials,
persistent storage, ingress, host networking, or external runtime network
dependency.

The companion integration normally reaches a locally installed app at:

```text
http://local-ptbr-nlu:11555
```

If Home Assistant assigns a different internal hostname, enter that local
origin in the integration config flow.
