import hashlib
import math
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import scoring
from .db import get_session
from .models import Audit, Outlet, Photo, Rep
from .schemas import AuditIn, AuditOut, OutletScore, PhotoOut
from .security import current_rep, issue_token, supervisor, verify_password

PHOTO_DIR = os.environ.get('PHOTO_DIR', './photos')
MAX_PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_TYPES = {b'\xff\xd8\xff': 'image/jpeg', b'\x89PNG\r\n\x1a\n': 'image/png'}

app = FastAPI(title='Field Audit API', version='1.0.0',
              description='Outlet audits from field reps: scoring, geofence and photo checks.')


def to_out(a):
    return AuditOut(id=a.id, client_ref=a.client_ref, outlet_id=a.outlet_id, outlet_name=a.outlet.name,
                    rep=a.rep.username, visited_at=a.visited_at, distance_m=round(a.distance_m, 1), score=a.score,
                    flags=a.flags, photos=[PhotoOut(id=p.id, sha256=p.sha256, size_bytes=p.size_bytes,
                                                    duplicate_of_audit_id=p.duplicate_of_audit_id) for p in a.photos])


def load(db, audit_id):
    return db.scalar(select(Audit).where(Audit.id == audit_id)
                     .options(selectinload(Audit.photos), selectinload(Audit.outlet), selectinload(Audit.rep)))


@app.post('/token', tags=['auth'])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_session)):
    rep = db.scalar(select(Rep).where(Rep.username == form.username))
    if rep is None or not verify_password(form.password, rep.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'wrong username or password')
    return {'access_token': issue_token(rep), 'token_type': 'bearer'}


@app.get('/outlets/nearby', tags=['outlets'])
def nearby(lat: float, lng: float, radius_m: int = Query(1000, le=20000),
           db: Session = Depends(get_session), rep: Rep = Depends(current_rep)):
    # Cheap bounding box in SQL first, exact distance in Python after
    dlat = radius_m / 111_000
    dlng = dlat / max(0.1, abs(math.cos(math.radians(lat))))
    rows = db.scalars(select(Outlet).where(Outlet.lat.between(lat - dlat, lat + dlat),
                                           Outlet.lng.between(lng - dlng, lng + dlng))).all()
    found = [(scoring.distance_m(lat, lng, o.lat, o.lng), o) for o in rows]
    return [{'id': o.id, 'name': o.name, 'distance_m': round(d)} for d, o in sorted(found, key=lambda x: x[0])
            if d <= radius_m]


@app.post('/audits', response_model=AuditOut, status_code=201, tags=['audits'])
def submit(body: AuditIn, db: Session = Depends(get_session), rep: Rep = Depends(current_rep)):
    existing = db.scalar(select(Audit.id).where(Audit.rep_id == rep.id, Audit.client_ref == body.client_ref))
    if existing:
        return to_out(load(db, existing))

    outlet = db.get(Outlet, body.outlet_id)
    if outlet is None:
        raise HTTPException(404, 'outlet not found')
    try:
        score = scoring.score(body.answers)
    except scoring.AnswerError as e:
        raise HTTPException(422, str(e))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    visited = body.visited_at.astimezone(timezone.utc).replace(tzinfo=None) if body.visited_at.tzinfo else body.visited_at
    if visited > now + timedelta(minutes=5):
        raise HTTPException(422, 'visited_at is in the future')

    flags = []
    distance = scoring.distance_m(body.lat, body.lng, outlet.lat, outlet.lng)
    if distance > scoring.GEOFENCE_M:
        flags.append('outside_geofence')
    if now - visited > timedelta(days=2):
        flags.append('late_upload')
    # Two audits of the same outlet by the same rep on the same day usually means a mistake, or copying
    same_day = db.scalar(select(func.count()).select_from(Audit).where(
        Audit.rep_id == rep.id, Audit.outlet_id == outlet.id,
        func.date(Audit.visited_at) == visited.date().isoformat()))
    if same_day:
        flags.append('repeat_visit_same_day')

    audit = Audit(client_ref=body.client_ref, rep_id=rep.id, outlet_id=outlet.id, visited_at=visited, received_at=now,
                  lat=body.lat, lng=body.lng, distance_m=distance, answers=body.answers, score=score, flags=flags)
    db.add(audit)
    try:
        db.commit()
    except IntegrityError:
        # The same retry arrived twice at once; the other request won
        db.rollback()
        existing = db.scalar(select(Audit.id).where(Audit.rep_id == rep.id, Audit.client_ref == body.client_ref))
        return to_out(load(db, existing))
    return to_out(load(db, audit.id))


@app.post('/audits/{audit_id}/photos', response_model=PhotoOut, status_code=201, tags=['audits'])
async def upload_photo(audit_id: int, file: UploadFile, db: Session = Depends(get_session),
                       rep: Rep = Depends(current_rep)):
    audit = load(db, audit_id)
    if audit is None or (audit.rep_id != rep.id and not rep.is_supervisor):
        raise HTTPException(404, 'audit not found')

    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, 'photo is larger than 8 MB')
    # Trust the bytes, not the file name or the Content-Type header
    content_type = next((t for magic, t in PHOTO_TYPES.items() if data.startswith(magic)), None)
    if content_type is None:
        raise HTTPException(415, 'only JPEG and PNG photos are accepted')

    digest = hashlib.sha256(data).hexdigest()
    same_audit = next((p for p in audit.photos if p.sha256 == digest), None)
    if same_audit:
        return PhotoOut(id=same_audit.id, sha256=digest, size_bytes=same_audit.size_bytes,
                        duplicate_of_audit_id=same_audit.duplicate_of_audit_id)

    # The same photo on a different audit is the classic way of faking a visit
    earlier = db.scalar(select(Photo.audit_id).where(Photo.sha256 == digest, Photo.audit_id != audit_id).limit(1))
    os.makedirs(PHOTO_DIR, exist_ok=True)
    path = os.path.join(PHOTO_DIR, digest + ('.jpg' if content_type == 'image/jpeg' else '.png'))
    if not os.path.exists(path):
        with open(path, 'wb') as f:
            f.write(data)

    photo = Photo(audit_id=audit_id, sha256=digest, content_type=content_type, size_bytes=len(data), path=path,
                  duplicate_of_audit_id=earlier)
    audit.photos.append(photo)
    if earlier and 'reused_photo' not in audit.flags:
        audit.flags = audit.flags + ['reused_photo']
    db.commit()
    return PhotoOut(id=photo.id, sha256=digest, size_bytes=len(data), duplicate_of_audit_id=earlier)


@app.get('/audits', response_model=list[AuditOut], tags=['audits'])
def list_audits(flagged: bool = False, outlet_id: int | None = None, limit: int = Query(50, le=500),
                db: Session = Depends(get_session), rep: Rep = Depends(current_rep)):
    q = select(Audit).options(selectinload(Audit.photos), selectinload(Audit.outlet), selectinload(Audit.rep))
    if not rep.is_supervisor:
        q = q.where(Audit.rep_id == rep.id)
    if outlet_id:
        q = q.where(Audit.outlet_id == outlet_id)
    audits = db.scalars(q.order_by(Audit.visited_at.desc()).limit(limit if not flagged else 5000)).all()
    if flagged:
        audits = [a for a in audits if a.flags][:limit]
    return [to_out(a) for a in audits]


@app.get('/reports/outlet-scores', response_model=list[OutletScore], tags=['reports'])
def outlet_scores(db: Session = Depends(get_session), sup: Rep = Depends(supervisor)):
    rows = db.execute(select(Audit.outlet_id, Outlet.name, Audit.score, Audit.visited_at)
                      .join(Outlet).order_by(Audit.outlet_id, Audit.visited_at)).all()
    by_outlet = {}
    for outlet_id, name, score, _ in rows:
        by_outlet.setdefault((outlet_id, name), []).append(score)
    return [OutletScore(outlet_id=oid, outlet_name=name, audits=len(s), average_score=round(sum(s) / len(s), 1),
                        latest_score=s[-1], trend=round(s[-1] - s[-2], 1) if len(s) > 1 else None)
            for (oid, name), s in sorted(by_outlet.items())]
