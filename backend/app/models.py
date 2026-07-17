from typing import Optional
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
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


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    plates: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[str] = mapped_column(String(32))
    # Library index fields (filesystem is the source of truth; these cache it).
    relative_path: Mapped[str] = mapped_column(String(1024), default="")
    folder: Mapped[str] = mapped_column(String(1024), default="/")
    size_bytes: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)


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
    # --- Estimate values (set after background test slice) ---
    estimate_token: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimate_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    estimate_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimate_filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimate_filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    estimate_preset_label: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class JobPrinterConfig(Base):
    __tablename__ = "job_printer_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    print_profile: Mapped[str] = mapped_column(String(512))
    filament_profile: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    filament_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    filament_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    filament_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tool_index: Mapped[Optional[int]] = mapped_column(nullable=True)
    filament_map: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    slice_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    slice_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GcodeFile(Base):
    __tablename__ = "gcode_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    path: Mapped[str] = mapped_column(String(1024))
    filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class QueueConfig(Base):
    __tablename__ = "queue_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=5)
    operator_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    snapshot_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    estimates_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class SpoolmanConfig(Base):
    __tablename__ = "spoolman_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    api_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


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
    # Legacy OrcaSlicer fields kept for backward compat with pre-v005 rows
    color_hex: Mapped[str] = mapped_column(String(7), default="#FFFFFF")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WebhookConfig(Base):
    __tablename__ = "webhook_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    secret: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)


class ProjectLink(Base):
    __tablename__ = "project_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2048))
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class JobItemFailure(Base):
    __tablename__ = "job_item_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    project_item_id: Mapped[int] = mapped_column(ForeignKey("project_items.id", ondelete="CASCADE"))
    quantity_failed: Mapped[int] = mapped_column(Integer)
    quantity_on_plate: Mapped[int] = mapped_column(Integer)
