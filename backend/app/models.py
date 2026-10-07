from typing import Optional
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    printer_type: Mapped[str] = mapped_column(String(50))
    connection_config: Mapped[dict] = mapped_column(JSON)
    awaiting_plate_clear: Mapped[bool] = mapped_column(Boolean, default=False)
    orca_printer_profiles: Mapped[list] = mapped_column(JSON, default=list)
    current_orca_printer_profile: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    queue_on: Mapped[bool] = mapped_column(Boolean, default=True)
    loaded_filaments: Mapped[list] = mapped_column(JSON, default=list)
    build_plate_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    no_snapshots_while_idle: Mapped[bool] = mapped_column(Boolean, default=False)
    bed_x_mm: Mapped[float] = mapped_column(Float, default=256.0)
    bed_y_mm: Mapped[float] = mapped_column(Float, default=256.0)
    # Server-local 'HH:MM' window in which this printer won't start new jobs (wraps midnight); both or neither.
    quiet_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    quiet_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    lifetime_job_count: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_print_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # Per-printer override of the shop-wide machine rate ($ per hour of print time); null = use the shop rate.
    machine_rate_per_hour: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    # Legacy only: an absolute filesystem path, kept solely so migrate_legacy_uploads()
    # can locate pre-library-index rows (relative_path == "") that predate it. Never
    # written for any row that has relative_path set — the absolute path on disk is
    # always computed fresh via library_scanner.library_abs_path(), since the library
    # root differs between local dev and the container and a persisted absolute path
    # from one is not valid in the other.
    stored_path: Mapped[str] = mapped_column(String(1024), default="")
    plates: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[str] = mapped_column(String(32))
    # Library index fields (filesystem is the source of truth; these cache it).
    relative_path: Mapped[str] = mapped_column(String(1024), default="")
    folder: Mapped[str] = mapped_column(String(1024), default="/")
    size_bytes: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    # Project-generated packs only (BIZ-193): hash of the pack inputs (STL hashes x quantities, bed, pack mode), so an
    # identical later generation can reuse this 3MF instead of re-packing.
    pack_recipe_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class SlicedVersion(Base):
    """A cached slice (BIZ-191): the library file `file_id` (.gcode / .gcode.3mf) is what the model `source_file_id`
    sliced to with these settings. `cache_key` hashes every input that changes the output (see services/slice_cache);
    the preset-content hash and slicer version are kept apart from it and only decide whether the version is stale."""
    __tablename__ = "sliced_versions"
    # Never reuse an id: jobs.sliced_version_id must not silently start pointing at a newer, unrelated version.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), unique=True)
    source_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True, index=True)
    source_content_hash: Mapped[str] = mapped_column(String(64), default="")
    plate_number: Mapped[int] = mapped_column(Integer, default=1)
    machine_preset: Mapped[str] = mapped_column(String(255))
    process_preset: Mapped[str] = mapped_column(String(512))
    filament_presets: Mapped[list] = mapped_column(JSON, default=list)
    extra_config: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    filament_map: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    artifact_kind: Mapped[str] = mapped_column(String(16))   # gcode | gcode_3mf
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    preset_content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    slicer_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filament_type: Mapped[str] = mapped_column(String(100), default="any")
    filament_color: Mapped[str] = mapped_column(String(20), default="any")
    estimated_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_from_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # plain int: jobs get deleted
    created_at: Mapped[str] = mapped_column(String(32))


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#64748b")
    category: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class FileTag(Base):
    __tablename__ = "file_tags"

    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_type: Mapped[str] = mapped_column(String(20))  # "customer" | "internal"
    customer: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    due_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    parts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
    # Payment tracking, for future profit/loss reporting.
    amount_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid", server_default="unpaid")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    plate_number: Mapped[int] = mapped_column(default=1)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    assigned_printer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("printers.id"), nullable=True)
    queue_position: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    block_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
    completed_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    project_item_quantities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # --- Actual values (set at production slice time, before GcodeFile deleted) ---
    actual_filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    deduction_skipped: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Why filament usage was not recorded for this job (inventory tracking suspended / no starting weight); null otherwise.
    deduction_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # --- Estimate values (set after background test slice) ---
    estimate_token: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimate_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    estimate_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimate_filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimate_filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    estimate_preset_label: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Manually-entered cost of the filament used for this job, for profit/loss reporting.
    filament_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The printer this job actually ran on. Unlike assigned_printer_id it survives failure/cancel, so fleet
    # analytics can attribute outcomes to a printer.
    # Plain integer (no FK — see v025); delete_printer nulls it.
    printed_on_printer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # UTC ISO instant before which the queue engine won't start this job (None = as soon as possible).
    not_before: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # --- Slicing cache (BIZ-191) ---
    # Save this job's production slice to the library as a cached version, under this name (None = default name).
    save_slice: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    save_slice_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # When a printer claims the job, print a matching cached version instead of slicing.
    allow_cached_slice: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # The cached version this job prints / printed. Plain int (an FK column can't be dropped by SQLite's ALTER, so
    # v033 couldn't be rolled back); safe because sliced_versions never reuses an id (AUTOINCREMENT) — a deleted
    # version leaves a pointer that resolves to nothing, never to a newer, unrelated version.
    sliced_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Latest cache decision + save outcome, for debugging (shape: services/slice_cache.py).
    slice_cache_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class JobPrinterConfig(Base):
    __tablename__ = "job_printer_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    print_profile: Mapped[str] = mapped_column(String(512))
    filament_profile: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    filament_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    # Provider-namespaced "specific material" ask (v036); `filament_id` is the legacy Spoolman mirror, written while the provider is Spoolman
    material_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    material_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Filament ask — "any" means no constraint (never null/blank; see v012 migration)
    filament_type: Mapped[str] = mapped_column(String(100), nullable=False, server_default="any")
    filament_color: Mapped[str] = mapped_column(String(20), nullable=False, server_default="any")
    tool_index: Mapped[Optional[int]] = mapped_column(nullable=True)
    filament_map: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    slice_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    slice_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set when this row was materialized from a JobModelTarget ("any printer of this model"); null = explicit pick.
    # Plain integer, no FK (see v025/v031): SQLite can't DROP a column inside a table-level FOREIGN KEY, and the
    # rows are always deleted with their target explicitly.
    model_target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # One config per (job, printer); created by v031 for existing DBs, declared here for fresh ones.
    __table_args__ = (
        Index("ux_job_printer_configs_job_printer", "job_id", "printer_id", unique=True),
        Index("ux_job_printer_configs_target_printer", "model_target_id", "printer_id", unique=True,
              sqlite_where=text("model_target_id IS NOT NULL")),
    )


class JobModelTarget(Base):
    """A job eligible on *any* printer whose `current_orca_printer_profile` equals `machine_profile`. The queue
    engine materializes it into per-printer `JobPrinterConfig` rows (services/model_targets.py), so everything
    downstream keeps looking configs up by (job, printer). Slot-specific asks (tool_index) don't apply here."""
    __tablename__ = "job_model_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    machine_profile: Mapped[str] = mapped_column(String(255), index=True)
    print_profile: Mapped[str] = mapped_column(String(512))
    filament_profile: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    filament_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    material_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    material_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    filament_type: Mapped[str] = mapped_column(String(100), default="any", server_default="any")
    filament_color: Mapped[str] = mapped_column(String(20), default="any", server_default="any")
    filament_map: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class GcodeFile(Base):
    __tablename__ = "gcode_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    path: Mapped[str] = mapped_column(String(1024))
    filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The slicing-cache key inputs this artifact was sliced from (slice_cache.CacheKeyInputs.as_dict; null for a
    # pre-sliced file or an uncacheable source) — so a job flagged "save" after slicing can still be saved (BIZ-192).
    slice_inputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class QueueConfig(Base):
    __tablename__ = "queue_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=5)
    operator_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    snapshot_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    estimates_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Lowest alarm severity that raises a `printer.alarm` webhook/notification (info < warning < error < fatal).
    alarm_min_severity: Mapped[str] = mapped_column(String(10), default="warning", server_default="warning")
    # Slicing cache (BIZ-191): True = automatic reuse reslices a cached version whose presets/slicer changed since it
    # was sliced; False = it is still printed (flagged stale), pinning a known-good print across profile updates.
    slice_cache_use_latest_settings: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class CostConfig(Base):
    """Shop-wide cost model (singleton id=1): hourly machine and labour rates, applied live to every project's
    expenses (changing a rate re-prices past jobs — nothing is snapshotted at print time)."""
    __tablename__ = "cost_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_rate_per_hour: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    labour_rate_per_hour: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class SpoolmanConfig(Base):
    __tablename__ = "spoolman_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    api_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    # last_sync_at: last time a sync attempt *succeeded*. last_attempt_at: last
    # attempt regardless of outcome (drives the sync-interval polling cadence).
    last_sync_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_attempt_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_sync_error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Low-inventory alerts (event `spool.low`): grams below which a spool alerts. A per-filament override
    # ({spoolman filament id (str): grams}) wins over the default; neither set = no alerts.
    low_stock_default_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_stock_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Spool ids already alerted while below their threshold, so each drop alerts once (cleared on refill).
    low_stock_alerted: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(32))
    phone: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


PROJECT_STAGES = ("draft", "planning", "queued")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    customer: Mapped[str] = mapped_column(String(255), default="")
    order_type: Mapped[str] = mapped_column(String(20), default="internal")  # "customer" | "internal"
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    due_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # machine_uuid / process_uuid kept for backward-compat (legacy generate flow); not shown in UI
    machine_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    process_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    source_app: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_user: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_layout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
    share_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    share_token_created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Payment tracking, for future profit/loss reporting.
    amount_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Quoted total for the project; outstanding balance = price - amount_paid.
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid", server_default="unpaid")
    # Whether the customer portal shows this project's quote (price, paid, balance). Staff decide; default hidden.
    price_visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # When the customer accepted the shown quote; cleared if the price changes afterwards.
    quote_accepted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    # Set by migration v032 when this project was created from a legacy customer order (provenance; plain int).
    converted_from_order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("share_token", name="uq_projects_share_token"),)


class ProjectItem(Base):
    __tablename__ = "project_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="RESTRICT")
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    quantity_completed: Mapped[int] = mapped_column(Integer, default=0)
    quantity_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Filament requirement spec — "any" means no constraint
    filament_type: Mapped[str] = mapped_column(String(50), default="any")
    filament_color: Mapped[str] = mapped_column(String(20), default="any")
    filament_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    material_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    material_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Legacy OrcaSlicer fields kept for backward compat with pre-v005 rows
    color_hex: Mapped[str] = mapped_column(String(7), default="#FFFFFF")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WebhookConfig(Base):
    __tablename__ = "webhook_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    secret: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)


class NotificationConfig(Base):
    __tablename__ = "notification_config"

    id: Mapped[int] = mapped_column(primary_key=True)

    ntfy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ntfy_server_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    ntfy_topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ntfy_priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ntfy_events: Mapped[list] = mapped_column(JSON, default=list)

    discord_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    discord_webhook_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    discord_events: Mapped[list] = mapped_column(JSON, default=list)

    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    email_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_password: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    email_from_addr: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_to_addrs: Mapped[list] = mapped_column(JSON, default=list)
    email_events: Mapped[list] = mapped_column(JSON, default=list)


class ProjectLink(Base):
    __tablename__ = "project_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2048))
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default="")


PAYMENT_METHODS = ("cash", "card", "bank_transfer", "check", "other")


class ProjectPayment(Base):
    """One payment received against a project. Once a project has any, its `amount_paid` and
    `payment_status` are derived from these rows (see services/payments.py)."""
    __tablename__ = "project_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    received_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD — the day the money arrived
    method: Mapped[str] = mapped_column(String(20), default="other", server_default="other")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ProjectLabor(Base):
    """Time spent on a project that isn't machine time (setup, post-processing, assembly, packing)."""
    __tablename__ = "project_labor"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logged_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ProjectPart(Base):
    """A non-3D-printed part (bought/off-the-shelf hardware) needed to complete a project's
    assembly, e.g. "3mm magnet" x5. `allocated` is a manual yes/no flag set by the user."""
    __tablename__ = "project_parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    allocated: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default="")
    # Cost of one unit (bought-in hardware); the project's parts expense is quantity × unit_cost.
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class JobItemFailure(Base):
    __tablename__ = "job_item_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    project_item_id: Mapped[int] = mapped_column(ForeignKey("project_items.id", ondelete="CASCADE"))
    quantity_failed: Mapped[int] = mapped_column(Integer)
    quantity_on_plate: Mapped[int] = mapped_column(Integer)


class MaintenanceItem(Base):
    __tablename__ = "maintenance_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(20), default="general")  # "general" | "model"
    machine_vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    machine_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class MaintenanceTrigger(Base):
    __tablename__ = "maintenance_triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    maintenance_item_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE")
    )
    trigger_type: Mapped[str] = mapped_column(String(20))  # "calendar" | "job_time" | "job_count"
    amount: Mapped[float] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # calendar only


class PrinterMaintenanceState(Base):
    __tablename__ = "printer_maintenance_state"
    __table_args__ = (UniqueConstraint("printer_id", "maintenance_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    maintenance_item_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE")
    )
    last_done_at: Mapped[str] = mapped_column(String(32))
    baseline_job_count: Mapped[int] = mapped_column(Integer, default=0)
    baseline_print_seconds: Mapped[int] = mapped_column(Integer, default=0)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_prefix", name="uq_api_keys_prefix"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, 64 chars
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(32))
    last_used_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Set for customer login sessions; NULL for staff/integration API keys.
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    # True for admin login sessions: hidden from the key list, revoked on admin password change.
    admin_session: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class AdminAccount(Base):
    """Singleton (id=1), created on first boot with no password. See docs/agent/conventions.md."""
    __tablename__ = "admin_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="admin")
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    allow_local_login: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    recovery_code_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    recovery_code_expires_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PrinterAlarm(Base):
    """One problem a printer reported (Bambu HMS, Klipper shutdown, SDCP error number…). A row is *active* while
    the printer keeps reporting its `code`; it is resolved (not deleted) when the printer stops, so the table is
    the alarm history. `acknowledged_at` only silences it in the UI — it does not affect resolution."""
    __tablename__ = "printer_alarms"
    __table_args__ = (
        # One ACTIVE row per (printer, code): makes a racing double-insert fail loudly instead of duplicating.
        Index("ux_printer_alarms_active", "printer_id", "code", unique=True, sqlite_where=text("resolved_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(10))            # info | warning | error | fatal
    message: Mapped[str] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)   # e.g. "hms", "klipper", "sdcp"
    help_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    first_seen: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[str] = mapped_column(String(32))
    resolved_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    acknowledged_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class PluginConfig(Base):
    """Per-plugin settings, secrets (write-only through every API) and runtime state (health, last error)."""
    __tablename__ = "plugin_configs"

    plugin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    secrets: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class CapabilitySelection(Base):
    """Which plugin serves a capability. `explicit` = the user chose (including "None"); False = auto-selected because it was
    the only enabled provider. A provider is *active* iff the row names it AND it is enabled AND its requirements are met."""
    __tablename__ = "capability_selections"

    capability: Mapped[str] = mapped_column(String(96), primary_key=True)
    plugin_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)


class PluginSchemaVersion(Base):
    """Applied plugin-owned migrations (see plugins/migrations.py)."""
    __tablename__ = "plugin_schema_versions"

    plugin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    applied_at: Mapped[str] = mapped_column(String(32), server_default=text("(strftime('%Y-%m-%dT%H:%M:%S', 'now'))"))


class InstalledPlugin(Base):
    """An installed (non-bundled) plugin package: `/data/plugins/<id>/<version>/`. Bundled plugins have no row.
    status: pending_restart | active | error | pending_removal (see plugins/installer.py)."""
    __tablename__ = "installed_plugins"

    plugin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    publisher: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(16))                       # upload | github
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    subdir: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    archive_sha256: Mapped[str] = mapped_column(String(64))
    installed_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    """Who did what to the instance itself (plugin install/upgrade/uninstall, restart). Append-only."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class InventoryConfig(Base):
    """Provider-agnostic inventory settings (singleton id=1). Low-stock keys are namespaced `"<provider>:<ref>"`."""
    __tablename__ = "inventory_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    deduct_on_complete: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    low_stock_default_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_stock_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)     # {"spoolman:3": 40.0}
    low_stock_alerted: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)       # ["spoolman:7"]


class JobSpoolSnapshot(Base):
    """A spool's weight when a job started printing (the base for the absolute `pre - spent` write at completion).
    `source` = live | pending | cached | missing (missing: no starting weight could be obtained -> tracking suspends)."""
    __tablename__ = "job_spool_snapshots"
    __table_args__ = (UniqueConstraint("job_id", "provider", "spool_ref", name="ux_job_spool_snapshots"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    printer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    spool_ref: Mapped[str] = mapped_column(String(128))
    pre_weight_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    taken_at: Mapped[str] = mapped_column(String(32))


class InventoryPendingWrite(Base):
    """The deduction outbox: an ABSOLUTE `set remaining = target_g` for a spool, written in the completion transaction and
    flushed to the provider by the host (re-sending is harmless). status: pending -> applied | superseded | discarded."""
    __tablename__ = "inventory_pending_writes"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    spool_ref: Mapped[str] = mapped_column(String(128))
    target_g: Mapped[float] = mapped_column(Float)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    printer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(24))                      # queue | manual_complete
    created_at: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_attempt_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")

    __table_args__ = (Index("ix_inventory_pending_writes_spool", "provider", "spool_ref", "status"),)


class InventorySpoolStatus(Base):
    """A spool whose usage tracking is suspended (its recorded weight is known to be wrong) until a user corrects it."""
    __tablename__ = "inventory_spool_status"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    spool_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    tracking: Mapped[str] = mapped_column(String(16), default="suspended")
    reason: Mapped[str] = mapped_column(Text)
    since: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class InventoryCache(Base):
    """Last-known list of a REMOTE provider's `spools` / `materials` (so reads survive an outage and a restart)."""
    __tablename__ = "inventory_cache"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)          # spools | materials
    payload: Mapped[list] = mapped_column(JSON)
    fetched_at: Mapped[str] = mapped_column(String(32))
