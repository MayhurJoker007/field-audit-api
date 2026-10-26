import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import scoring
from app.db import get_session
from app.main import app
from app.models import Base, Outlet, Rep
from app.security import hash_password

FULL = {
    'fridge_present': True, 'fridge_clean': True, 'fridge_brand_share': 80, 'price_board_visible': True,
    'core_range_in_stock': 6, 'promo_material': 'current', 'out_of_date_stock': False,
}
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 200
OUTLET = (-25.72061, 28.39580)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('app.main.PHOTO_DIR', str(tmp_path))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as db:
        db.add_all([
            Rep(username='thabo', full_name='Thabo', password_hash=hash_password('pw-thabo')),
            Rep(username='lerato', full_name='Lerato', password_hash=hash_password('pw-lerato')),
            Rep(username='boss', full_name='Boss', password_hash=hash_password('pw-boss'), is_supervisor=True),
            Outlet(id=1, name='Mamelodi Corner Store', lat=OUTLET[0], lng=OUTLET[1]),
            Outlet(id=2, name='Far Away Spaza', lat=-26.2, lng=28.0),
        ])
        db.commit()

    def session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = session
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client, user):
    r = client.post('/token', data={'username': user, 'password': 'pw-' + user})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def audit(ref='phone-0001', outlet=1, lat=OUTLET[0], lng=OUTLET[1], answers=None, when=None):
    return {'client_ref': ref, 'outlet_id': outlet, 'lat': lat, 'lng': lng, 'answers': answers or FULL,
            'visited_at': (when or datetime.now(timezone.utc)).isoformat()}


# --- scoring ---------------------------------------------------------------

def test_perfect_and_partial_scores():
    perfect = dict(FULL, fridge_brand_share=100, core_range_in_stock=8)
    assert scoring.score(perfect) == 100.0
    # 15 + 10 + 16 + 10 + 18.75 + 10 + 10 = 89.75 of 100
    assert scoring.score(FULL) == 89.8


def test_no_fridge_skips_fridge_questions_but_loses_their_points():
    answers = {k: v for k, v in FULL.items() if not k.startswith('fridge')}
    answers['fridge_present'] = False
    # 0 + 0 + 0 + 10 + 18.75 + 10 + 10 = 48.75
    assert scoring.score(answers) == 48.8


@pytest.mark.parametrize('change, message', [
    ({'core_range_in_stock': 9}, 'whole number from 0 to 8'),
    ({'core_range_in_stock': True}, 'whole number'),
    ({'promo_material': 'shiny'}, 'one of'),
    ({'fridge_brand_share': 120}, '0 to 100'),
    ({'favourite_colour': 'blue'}, 'unknown questions'),
])
def test_bad_answers(change, message):
    with pytest.raises(scoring.AnswerError, match=message):
        scoring.score(dict(FULL, **change))


# --- API -------------------------------------------------------------------

def test_login_required_and_wrong_password(client):
    assert client.get('/audits').status_code == 401
    assert client.post('/token', data={'username': 'thabo', 'password': 'nope'}).status_code == 401


def test_submit_scores_on_the_server(client):
    r = client.post('/audits', json=audit(), headers=login(client, 'thabo'))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body['score'] == 89.8
    assert body['flags'] == []
    assert body['distance_m'] < 1


def test_retried_upload_does_not_create_a_second_audit(client):
    h = login(client, 'thabo')
    first = client.post('/audits', json=audit(ref='retry-abc-123'), headers=h).json()
    second = client.post('/audits', json=audit(ref='retry-abc-123'), headers=h).json()
    assert first['id'] == second['id']
    assert len(client.get('/audits', headers=h).json()) == 1


def test_flags_outside_geofence_late_and_repeat_visits(client):
    h = login(client, 'thabo')
    far = client.post('/audits', json=audit(ref='far-0000001', lat=OUTLET[0] + 0.01), headers=h).json()
    assert 'outside_geofence' in far['flags']

    old = client.post('/audits', json=audit(ref='late-000001', outlet=2, lat=-26.2, lng=28.0,
                                            when=datetime.now(timezone.utc) - timedelta(days=4)), headers=h).json()
    assert old['flags'] == ['late_upload']

    again = client.post('/audits', json=audit(ref='again-00001'), headers=h).json()
    assert 'repeat_visit_same_day' in again['flags']


def test_rejects_future_visits_and_bad_answers(client):
    h = login(client, 'thabo')
    future = audit(ref='future-0001', when=datetime.now(timezone.utc) + timedelta(hours=2))
    assert client.post('/audits', json=future, headers=h).status_code == 422
    bad = audit(ref='bad-answers', answers=dict(FULL, promo_material='shiny'))
    assert 'promo_material' in client.post('/audits', json=bad, headers=h).json()['detail']


def test_photo_reused_on_another_audit_is_flagged(client):
    thabo = login(client, 'thabo')
    lerato = login(client, 'lerato')
    a1 = client.post('/audits', json=audit(ref='thabo-00001'), headers=thabo).json()
    a2 = client.post('/audits', json=audit(ref='lerato-0001'), headers=lerato).json()

    p1 = client.post(f"/audits/{a1['id']}/photos", files={'file': ('a.jpg', io.BytesIO(JPEG), 'image/jpeg')}, headers=thabo)
    assert p1.status_code == 201 and p1.json()['duplicate_of_audit_id'] is None

    p2 = client.post(f"/audits/{a2['id']}/photos", files={'file': ('b.jpg', io.BytesIO(JPEG), 'image/jpeg')}, headers=lerato)
    assert p2.json()['duplicate_of_audit_id'] == a1['id']

    flagged = client.get('/audits?flagged=true', headers=login(client, 'boss')).json()
    assert [a['id'] for a in flagged] == [a2['id']]
    assert flagged[0]['flags'] == ['reused_photo']


def test_photo_checks(client):
    h = login(client, 'thabo')
    a = client.post('/audits', json=audit(), headers=h).json()
    fake = client.post(f"/audits/{a['id']}/photos", files={'file': ('x.jpg', io.BytesIO(b'GIF89a...'), 'image/jpeg')},
                       headers=h)
    assert fake.status_code == 415
    other = client.post(f"/audits/{a['id']}/photos", files={'file': ('x.jpg', io.BytesIO(JPEG), 'image/jpeg')},
                        headers=login(client, 'lerato'))
    assert other.status_code == 404


def test_reps_only_see_their_own_audits(client):
    client.post('/audits', json=audit(ref='thabo-00001'), headers=login(client, 'thabo'))
    assert client.get('/audits', headers=login(client, 'lerato')).json() == []
    assert len(client.get('/audits', headers=login(client, 'boss')).json()) == 1


def test_outlet_score_report_is_for_supervisors(client):
    h = login(client, 'thabo')
    client.post('/audits', json=audit(ref='day1-000001', when=datetime.now(timezone.utc) - timedelta(days=1)), headers=h)
    client.post('/audits', json=audit(ref='day2-000001', answers=dict(FULL, core_range_in_stock=8)), headers=h)

    assert client.get('/reports/outlet-scores', headers=h).status_code == 403
    report = client.get('/reports/outlet-scores', headers=login(client, 'boss')).json()
    assert report == [{'outlet_id': 1, 'outlet_name': 'Mamelodi Corner Store', 'audits': 2,
                       'average_score': 92.9, 'latest_score': 96.0, 'trend': 6.2}]


def test_nearby_outlets(client):
    found = client.get('/outlets/nearby', params={'lat': OUTLET[0] + 0.001, 'lng': OUTLET[1]},
                       headers=login(client, 'thabo')).json()
    assert [o['id'] for o in found] == [1]
    assert 100 < found[0]['distance_m'] < 120
