from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Rep(Base):
    __tablename__ = 'rep'

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(120))
    is_supervisor: Mapped[bool] = mapped_column(default=False)


class Outlet(Base):
    __tablename__ = 'outlet'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float]
    lng: Mapped[float]
    venue_type: Mapped[str | None] = mapped_column(String(50))


class Audit(Base):
    __tablename__ = 'audit'
    __table_args__ = (
        # The phone app retries uploads on bad signal, so the same client id must not create two audits
        UniqueConstraint('rep_id', 'client_ref', name='uq_audit_client_ref'),
        Index('ix_audit_outlet_visited', 'outlet_id', 'visited_at'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ref: Mapped[str] = mapped_column(String(64))
    rep_id: Mapped[int] = mapped_column(ForeignKey('rep.id'))
    outlet_id: Mapped[int] = mapped_column(ForeignKey('outlet.id'))
    visited_at: Mapped[datetime]
    received_at: Mapped[datetime]
    lat: Mapped[float]
    lng: Mapped[float]
    distance_m: Mapped[float]
    answers: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float]
    flags: Mapped[list] = mapped_column(JSON, default=list)

    rep: Mapped[Rep] = relationship()
    outlet: Mapped[Outlet] = relationship()
    photos: Mapped[list['Photo']] = relationship(back_populates='audit', cascade='all, delete-orphan')


class Photo(Base):
    __tablename__ = 'photo'

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey('audit.id'))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(50))
    size_bytes: Mapped[int]
    path: Mapped[str] = mapped_column(String(300))
    duplicate_of_audit_id: Mapped[int | None]

    audit: Mapped[Audit] = relationship(back_populates='photos')
