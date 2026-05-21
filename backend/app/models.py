from typing import Optional
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text
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


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    plates: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[str] = mapped_column(String(32))


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    plate_number: Mapped[int] = mapped_column(default=1)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    assigned_printer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("printers.id"), nullable=True)
    queue_position: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class JobPrinterConfig(Base):
    __tablename__ = "job_printer_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    print_profile: Mapped[str] = mapped_column(String(512))
    filament_profile: Mapped[str] = mapped_column(String(512))
    slice_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    slice_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GcodeFile(Base):
    __tablename__ = "gcode_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    path: Mapped[str] = mapped_column(String(1024))
