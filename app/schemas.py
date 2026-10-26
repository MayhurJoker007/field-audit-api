from datetime import datetime

from pydantic import BaseModel, Field


class AuditIn(BaseModel):
    client_ref: str = Field(min_length=8, max_length=64, description='id generated on the phone, used to ignore retries')
    outlet_id: int
    visited_at: datetime
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    answers: dict


class PhotoOut(BaseModel):
    id: int
    sha256: str
    size_bytes: int
    duplicate_of_audit_id: int | None


class AuditOut(BaseModel):
    id: int
    client_ref: str
    outlet_id: int
    outlet_name: str
    rep: str
    visited_at: datetime
    distance_m: float
    score: float
    flags: list[str]
    photos: list[PhotoOut]


class OutletScore(BaseModel):
    outlet_id: int
    outlet_name: str
    audits: int
    average_score: float
    latest_score: float
    trend: float | None = Field(description='latest minus previous score')
