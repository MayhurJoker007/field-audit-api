"""Creates demo reps and outlets. Run after `alembic upgrade head`.

    python seed.py
"""
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Outlet, Rep
from app.security import hash_password

OUTLETS = [
    ('Mamelodi Corner Store', -25.72061, 28.39580, 'spaza'),
    ('Lucky Star Tavern', -26.24850, 27.85400, 'tavern'),
    ('Ubuntu Liquors', -26.25020, 27.85710, 'liquor store'),
    ('Kasi Fresh Supermarket', -26.23710, 27.90820, 'supermarket'),
    ('Thembisa Tuck Shop', -25.99610, 28.22680, 'spaza'),
]

with SessionLocal() as db:
    if db.scalar(select(Rep).limit(1)) is None:
        db.add_all([
            Rep(username='thabo', full_name='Thabo Nkosi', password_hash=hash_password('field-demo-1')),
            Rep(username='lerato', full_name='Lerato Dlamini', password_hash=hash_password('field-demo-2')),
            Rep(username='supervisor', full_name='Anika van Wyk', password_hash=hash_password('field-demo-3'),
                is_supervisor=True),
        ])
        db.add_all([Outlet(name=n, lat=lat, lng=lng, venue_type=t) for n, lat, lng, t in OUTLETS])
        db.commit()
        print('seeded 3 reps and %d outlets' % len(OUTLETS))
    else:
        print('already seeded')
