# Operator guide

This guide covers the workflows available in the current Themis web app. Navigation and some controls depend on the active plugins, user role, and provider capabilities.

## Queue and jobs

The Queue shows active, queued, blocked, and recent jobs. Upload or select a model from the Files library to create a job. Configure its plate, eligible printers or printer-model targets, process profile, filament requirements, and optional order/project association. A job is sliced for the printer that claims it, by the printer's slicer: the default slicing plugin (Laminus today) unless you pick another for that printer in its editor (the choice appears once more than one slicer is enabled). If the chosen slicer is disabled, removed or down, that printer's jobs wait with a reason on the job; they are never sent to a different slicer. Eligible queued work is claimed automatically when a printer is available.

`blocked` means Themis cannot currently proceed (for example, no eligible ready printer, incompatible file, or slice failure); review the displayed reason and resolve it before retrying. `failed` records a terminal upload/start failure. A printer that has started a job waits for the operator to clear its plate and mark it ready before it can take another queued job. Use job details to inspect printer configurations, slice status, estimates, and available recovery actions.

Files that are already-sliced G-code are not sliced again. Themis surfaces compatibility information and warnings for these files; verify that the G-code is appropriate for the target machine before dispatch.

## Fleet and printers

Add printers using the models and connection fields provided by installed printer plugins. The Fleet view shows connection and print state, progress, camera and telemetry where supported, alarms, and whether the printer is waiting for plate clearance. Printer controls are capability-driven, so the available actions vary by integration. A disabled or unavailable printer plugin can leave its existing printer records dormant; re-enable or restore the integration to use them again.

Printer files and the printer console are separate views. Camera wall and printer snapshots require a camera capability and a reachable camera source. Camera streams are bounded by the server's configured stream limit.

## Files and filament

Files manages the model library, folders, search, tags, file details, and printer-model eligibility for pre-sliced files. Uploads are stored in Themis data storage. The Filament library appears when the active inventory provider advertises material or spool management capabilities. Spool tracking, profile links, label scanning, and inventory deduction depend on provider capabilities and settings.

## Orders, projects, and customers

Orders group work and expose their linked jobs. Projects organize parts, quantities, materials and reference links; project generation can prepare print jobs and associate them with an order when Laminus and the required profiles are available. Project detail also provides labor and payment records where enabled.

Customers have customer records and a restricted portal account. Customers see only the portal data associated with their account. Public project sharing uses an unguessable share link and is separate from customer sign-in. Treat shared links as access credentials.

## Maintenance, alarms, and analytics

Maintenance items can be scheduled using the supported date, completed-job-count, or print-time triggers. Fleet status surfaces due work. Alarms records printer-reported events and their resolution state. Analytics summarizes recorded fleet and job activity; its values depend on the history Themis has retained.

## Settings

Settings covers workshop and queue preferences, administrator account and local access, API keys, notifications and webhooks, printer/plugin management, inventory configuration, tags, maintenance, and data export/import options. Exact sections depend on installed plugins and capabilities. API keys should be granted only the scopes required by their integration; see [Integrations](integrations.md).
