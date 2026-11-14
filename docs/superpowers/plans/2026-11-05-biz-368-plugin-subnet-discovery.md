# BIZ-368: Printer plug-in subnet discovery

Expose existing vendor discovery in enabled printer plug-in settings. Derive availability from the client discovery hook, keeping vendor names out of core. Filter the existing discovery endpoint by optional plug-in ID, rejecting disabled/non-printer IDs before any probe.

Accept CIDR, single addresses, dotted masks, and trailing x/* octets. The settings default is `192.168.x.x`, a /16. Allow at most 65,536 addresses across all ranges within the existing LAN allowlist. Use 128 workers rather than a task per address. Keep the 45-second deadline for up to 4,096 hosts; allow 15 minutes above that and report truncation. Cancel and drain workers when the caller is cancelled.

Reuse the results panel and Add Printer wizard inline. Keep settings scan ranges/results in memory. Users explicitly choose a result and confirm a model when custom Moonraker firmware does not identify it. Discovery does not create printers or discover credentials. Keep deployment-specific addresses and hardware identity out of source, fixtures, logs, and tracker text.

Validation: virtual-network parser, /16 sweep/resource bounds, API plug-in isolation and disabled guards, settings visibility, wizard prefill, existing discovery tests, type-check/build, and a read-only live scan with redacted output. Update OpenAPI and discovery operator docs. Track decisions, validation, and limitations in BIZ-368.

Follow-up: allow users to edit each scan result's model and serial inline before adding. Keep original results for reset,
and hold overrides by printer type/IP in memory for the current scan. Cancel leaves applied values intact; reset restores
discovered values; rescanning clears overrides. A changed model invalidates any original model UUID. Serial overrides
also update connection_config.serial_number so explicit printer creation retains them. Preserve addresses, plug-in identity,
ports, and credentials. Unrecognized model names still require the existing model selection step. Cover replacement,
supplementing missing values, cancel/reset/rescan, stale identity rejection, and edited values in the creation request.
