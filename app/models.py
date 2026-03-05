"""SQLAlchemy ORM models for the SDC Time Tracker."""

import enum
from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, String, Boolean, Float, Date, DateTime,
    Enum, Text, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ── Enums ──────────────────────────────────────────────────────────────

class Role(str, enum.Enum):
    employee = "employee"
    supervisor = "supervisor"
    manager = "manager"


class EntryType(str, enum.Enum):
    check_in = "check_in"
    check_out = "check_out"


class LocationType(str, enum.Enum):
    office = "office"
    remote = "remote"
    offsite = "offsite"


class LeaveType(str, enum.Enum):
    vacation = "vacation"
    sick = "sick"


class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class AuthorizationStatus(str, enum.Enum):
    active = "active"
    used = "used"
    expired = "expired"


# ── Models ─────────────────────────────────────────────────────────────

class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), nullable=True)
    pin_hash = Column(String(255), nullable=False)
    role = Column(Enum(Role), default=Role.employee, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    pin_needs_reset = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    time_entries = relationship("TimeEntry", back_populates="employee", lazy="dynamic")
    offsite_entries = relationship("OffsiteEntry", back_populates="employee", lazy="dynamic")
    daily_summaries = relationship("DailySummary", back_populates="employee", lazy="dynamic")
    leave_requests = relationship("LeaveRequest", back_populates="employee",
                                  foreign_keys="LeaveRequest.employee_id", lazy="dynamic")
    remote_authorizations = relationship("RemoteAuthorization", back_populates="employee",
                                         foreign_keys="RemoteAuthorization.employee_id", lazy="dynamic")

    def __repr__(self):
        return f"<Employee {self.name} ({self.role.value})>"


class TimeEntry(Base):
    __tablename__ = "time_entries"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    declared_time = Column(DateTime, nullable=False)
    submission_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    entry_type = Column(Enum(EntryType), nullable=False)
    location_type = Column(Enum(LocationType), default=LocationType.office, nullable=False)
    is_remote = Column(Boolean, default=False)
    authorization_id = Column(Integer, ForeignKey("remote_authorizations.id"), nullable=True)
    comments = Column(Text, default="")

    # Relationships
    employee = relationship("Employee", back_populates="time_entries")
    authorization = relationship("RemoteAuthorization")

    def __repr__(self):
        return f"<TimeEntry {self.entry_type.value} {self.declared_time}>"


class OffsiteEntry(Base):
    __tablename__ = "offsite_entries"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    location = Column(String(200), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    comments = Column(Text, default="")
    submission_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    needs_review = Column(Boolean, default=False)

    # Relationships
    employee = relationship("Employee", back_populates="offsite_entries")


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (
        UniqueConstraint("employee_id", "date", name="uq_employee_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    total_hours = Column(Float, default=0.0)
    leave_hours = Column(Float, default=0.0)
    leave_type = Column(Enum(LeaveType), nullable=True)  # vacation or sick
    leave_approved = Column(Boolean, default=False)
    target_hours = Column(Float, nullable=False)
    is_compliant = Column(Boolean, default=False)
    lunch_end_of_day = Column(Boolean, default=False)
    lunch_approved = Column(Boolean, default=False)

    # Relationships
    employee = relationship("Employee", back_populates="daily_summaries")


class RemoteAuthorization(Base):
    __tablename__ = "remote_authorizations"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    authorized_by = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False)
    max_hours = Column(Float, nullable=False)
    location = Column(String(200), default="WFH")
    status = Column(Enum(AuthorizationStatus), default=AuthorizationStatus.active)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    employee = relationship("Employee", foreign_keys=[employee_id], back_populates="remote_authorizations")
    authorizer = relationship("Employee", foreign_keys=[authorized_by])


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    leave_type = Column(Enum(LeaveType), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(Enum(LeaveStatus), default=LeaveStatus.pending)
    approved_by = Column(Integer, ForeignKey("employees.id"), nullable=True)
    doctor_note_path = Column(String(500), nullable=True)
    comments = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    employee = relationship("Employee", foreign_keys=[employee_id], back_populates="leave_requests")
    approver = relationship("Employee", foreign_keys=[approved_by])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    action = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=True)
    old_values = Column(JSON, nullable=True)
    new_values = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    ip_address = Column(String(45), default="")

    # Relationships
    employee = relationship("Employee")


class AppSetting(Base):
    """Key-value store for application feature toggles and settings."""
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(500), nullable=False, default="")
    description = Column(String(500), nullable=True)

    def __repr__(self):
        return f"<AppSetting {self.key}={self.value}>"
