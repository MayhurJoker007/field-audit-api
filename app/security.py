import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .db import get_session
from .models import Rep

SECRET = os.environ.get('AUDIT_JWT_SECRET', 'dev-only-secret-change-me-0123456789abcdef')
TOKEN_MINUTES = 12 * 60    # a working day in the field, so reps don't get logged out mid-route
oauth2 = OAuth2PasswordBearer(tokenUrl='/token')

_ITERATIONS = 210_000


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _ITERATIONS)
    return 'pbkdf2_sha256$%d$%s$%s' % (_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password, stored):
    try:
        _, iterations, salt, digest = stored.split('$')
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(iterations))
    return hmac.compare_digest(test, base64.b64decode(digest))


def issue_token(rep):
    now = datetime.now(timezone.utc)
    claims = {'sub': str(rep.id), 'name': rep.username, 'sup': rep.is_supervisor,
              'iat': now, 'exp': now + timedelta(minutes=TOKEN_MINUTES)}
    return jwt.encode(claims, SECRET, algorithm='HS256')


def current_rep(token: str = Depends(oauth2), db: Session = Depends(get_session)):
    try:
        claims = jwt.decode(token, SECRET, algorithms=['HS256'])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'invalid or expired token',
                            headers={'WWW-Authenticate': 'Bearer'})
    rep = db.get(Rep, int(claims['sub']))
    if rep is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'unknown user')
    return rep


def supervisor(rep: Rep = Depends(current_rep)):
    if not rep.is_supervisor:
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'supervisors only')
    return rep
