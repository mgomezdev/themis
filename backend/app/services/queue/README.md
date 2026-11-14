# app/services/queue

`QueueEngine` (`app/services/queue_engine.py`) is one class; its methods are grouped here as mixins it inherits. Nothing in
this package is used on its own: `self` is always the engine, and every attribute a mixin touches (`self._factory`,
`self._mgr`, `self._executor`, `self._slice_queue`, ...) is set in `QueueEngine.__init__`.

| Module | Mixin | What is in it |
|---|---|---|
| *(queue_engine.py)* | `QueueEngine` | construction, `start`/`stop`, the loop, `_process_queue`, **claiming** (`_try_claim_for_printer`, head-of-line), pools, `_block_job`, task tracking (`_spawn`), and the module-level slice-request / notification-text helpers |
| `estimates.py` | `EstimatesMixin` | background estimate slices, `run_verify_slice`, saving a finished slice to the library |
| `liveness.py` | `LivenessMixin` | declaring an offline printer dead and releasing its work; blocking a job no printer could take (`_sweep_unmatched_jobs`) |
| `slicing.py` | `SlicingMixin` | `_run_slice_and_print`, the slicing cache, pre-sliced staging, `_handle_slice_failure` (blocked) and `_fail_job_post_slice` (failed) |
| `printing.py` | `PrintingMixin` | upload-and-start, resuming a sliced job, the plate-clear gate, `mark_printing` / `complete_job` / `fail_started_job` / `note_print_blocked`, `_reconcile_printing_jobs` |
| `events.py` | `EventsMixin` | the job event bus, webhooks, notification channels, the browser broadcast |

## The one rule

A mixin reads two kinds of names through the engine's module, as `_qe.<name>` (`import app.services.queue_engine as _qe`):

- anything `queue_engine.py` defines at module level (`_qe.logger`, `_qe._now()`, `_qe.build_slice_request`, `_qe.NO_MATCH_PREFIX`), and
- the collaborators listed in its `_PATCH_POINTS` (`notify`, `webhook_service`, `get_library_store`, `slicing_provider_for`,
  `get_format_provider`, `resolve_slicing`, `plugin_host`).

Why: tests replace those on `app.services.queue_engine` (`patch("app.services.queue_engine.notify")`), and log capture
filters on that module's logger name. Looking them up there at call time keeps one patch point for the whole engine; a
mixin importing `notify` itself would silently ignore the patch. Everything else (models, SQLAlchemy, other services) a
mixin imports directly.

`_qe` is only ever used inside a method body. `queue_engine.py` imports this package while it is still being
initialised, so a `_qe.<name>` at import time (a default argument, a decorator) would not exist yet.

## Adding a method

Put it in the mixin for its concern (or in `QueueEngine` if it is about claiming or the loop). If it needs a new
collaborator that tests will want to replace, import it in `queue_engine.py`, add it to `_PATCH_POINTS`, and call it as
`_qe.<name>`.
