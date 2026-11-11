"""UniEvent оқу нұсқасы: FastAPI + SQLite + JWT. Python 3.10+."""
import base64
import io
import math
import time
import pyotp
import qrcode
import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from webauthn import (generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json, base64url_to_bytes)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
    AuthenticatorAttachment, UserVerificationRequirement,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement)


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / 'frontend'
UPLOADS = ROOT / 'uploads'
UPLOADS.mkdir(exist_ok=True)
DB = ROOT / 'unievent.db'
SECRET = os.getenv('UNIEVENT_SECRET', '')
if not SECRET:
    raise RuntimeError('UNIEVENT_SECRET орнатыңыз (README қараңыз).')
app = FastAPI(title='UniEvent API')

@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 400_000)
    return salt.hex() + '$' + digest.hex()

def verify_password(password, saved):
    try:
        salt_hex, digest_hex = saved.split('$', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), 400_000)
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False

def setup():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','organizer','admin')),
            group_name TEXT DEFAULT '', password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL,
            location TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
            capacity INTEGER NOT NULL CHECK(capacity>0), organizer_id INTEGER NOT NULL,
            description TEXT DEFAULT '', is_tournament INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(organizer_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS registrations(
            event_id INTEGER NOT NULL, student_id INTEGER NOT NULL,
            PRIMARY KEY(event_id,student_id),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS feedbacks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            text TEXT NOT NULL, FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            filename TEXT NOT NULL, stored_name TEXT NOT NULL, sender_id INTEGER NOT NULL,
            sent_date TEXT NOT NULL, FOREIGN KEY(sender_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
            read_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournaments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            game TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Тіркелуде',
            description TEXT NOT NULL DEFAULT '', organizer_id INTEGER NOT NULL,
            FOREIGN KEY(organizer_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS passkeys(
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            public_key TEXT NOT NULL, sign_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS webauthn_challenges(
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, purpose TEXT NOT NULL,
            challenge TEXT NOT NULL, expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS face_ids(
            user_id INTEGER PRIMARY KEY, descriptor TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        ''')
        # Tournament upgrade: mark explicitly, without changing older event records.
        event_columns={row['name'] for row in c.execute('PRAGMA table_info(events)')}
        if 'is_tournament' not in event_columns:
            c.execute('ALTER TABLE events ADD COLUMN is_tournament INTEGER NOT NULL DEFAULT 0')
        # v3 migration, existing accounts and events remain unchanged.
        cols={row['name'] for row in c.execute('PRAGMA table_info(users)')}
        for col, ddl in [('mfa_secret','TEXT'),('mfa_pending','TEXT'),('mfa_last_step','INTEGER DEFAULT -1')]:
            if col not in cols: c.execute(f'ALTER TABLE users ADD COLUMN {col} {ddl}')
        # Демо аккаунттар тек орта айнымалылары арқылы жасалады.
        for role, prefix in [('admin','ADMIN'),('organizer','ORGANIZER')]:
            login = os.getenv(f'UNIEVENT_{prefix}_LOGIN')
            password = os.getenv(f'UNIEVENT_{prefix}_PASSWORD')
            if login and password and len(password) >= 8:
                c.execute('INSERT OR IGNORE INTO users(login,name,role,password_hash) VALUES(?,?,?,?)',
                    (login, 'Әкімші' if role=='admin' else 'Ұйымдастырушы',role,hash_password(password)))
setup()

class Login(BaseModel):
    login: str
    password: str

class Signup(BaseModel):
    login: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    group: str = Field(max_length=120)
    password: str = Field(min_length=8)

class RegistrationIn(BaseModel):
    group: str = Field(min_length=1, max_length=120)

class EventIn(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    category: str = Field(min_length=2, max_length=80)
    location: str = Field(min_length=2, max_length=150)
    date: str
    time: str
    capacity: int = Field(ge=1, le=1000)
    description: str = Field(default='', max_length=3000)
    is_tournament: bool = False

class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=1, max_length=1000)

def public_user(u):
    return {'id': u['id'], 'login':u['login'], 'name':u['name'], 'role':u['role'], 'group':u['group_name'], 'mfa_enabled':bool(u['mfa_secret'])}

def token_for(u, second_factor=False):
    return jwt.encode({'sub':str(u['id']), 'mfa':bool(second_factor),'exp':datetime.now(timezone.utc)+timedelta(hours=8)},SECRET,algorithm='HS256')

def require_user(authorization: str|None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'Алдымен жүйеге кіріңіз')
    try:
        data = jwt.decode(authorization[7:], SECRET, algorithms=['HS256'])
        ident = int(data['sub'])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401,'Сессия жарамсыз немесе мерзімі өтті')
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
    if not u:
        raise HTTPException(401,'Қолданушы табылмады')
    with db() as c:
        passkey_enabled = bool(c.execute('SELECT 1 FROM passkeys WHERE user_id=? LIMIT 1',(ident,)).fetchone())
        face_enabled = bool(c.execute('SELECT 1 FROM face_ids WHERE user_id=? LIMIT 1',(ident,)).fetchone())
    if (u['mfa_secret'] or passkey_enabled or face_enabled) and not (data.get('mfa') or data.get('passkey')):
        raise HTTPException(401,'Қосылған 2FA немесе Face ID арқылы растаңыз')
    return dict(u)

def role_user(*allowed):
    def dependency(user=Depends(require_user)):
        if user['role'] not in allowed:
            raise HTTPException(403,'Бұл әрекетке рұқсат жоқ')
        return user
    return dependency

@app.post('/api/signup')
def signup(payload: Signup):
    with db() as c:
        try:
            cur = c.execute('INSERT INTO users(login,name,role,group_name,password_hash) VALUES(?,?,?,?,?)',
                (payload.login.strip(),payload.name.strip(),'student',payload.group,hash_password(payload.password)))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Бұл студенттік ID тіркелген')
        u = c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone()
    return {'token':token_for(u),'user':public_user(u)}

# WebAuthn second factor: password is required before a passkey ceremony.
# Localhost is a trusted secure context. Phone access needs real HTTPS.
RP_ID = os.getenv('UNIEVENT_RP_ID', 'localhost')
ORIGIN = os.getenv('UNIEVENT_ORIGIN', 'http://localhost:8000')

class PasskeyTicket(BaseModel):
    ticket: str

class PasskeyProof(BaseModel):
    ticket: str
    ceremony: str
    credential: dict

def put_ceremony(user_id: int, purpose: str, challenge: bytes):
    cid = secrets.token_urlsafe(24)
    with db() as c:
        c.execute('DELETE FROM webauthn_challenges WHERE expires_at<?',(int(time.time()),))
        c.execute('INSERT INTO webauthn_challenges VALUES(?,?,?,?,?)',
                  (cid,user_id,purpose,base64.urlsafe_b64encode(challenge).decode().rstrip('='),int(time.time())+180))
    return cid

def take_ceremony(cid: str, user_id: int, purpose: str):
    with db() as c:
        row=c.execute('SELECT * FROM webauthn_challenges WHERE id=? AND user_id=? AND purpose=?',
                      (cid,user_id,purpose)).fetchone()
        c.execute('DELETE FROM webauthn_challenges WHERE id=?',(cid,))
    if not row or row['expires_at'] < int(time.time()):
        raise HTTPException(401,'Face ID растау мерзімі өтті. Қайта кіріңіз.')
    return base64url_to_bytes(row['challenge'])

def login_ticket_user(ticket: str, purpose='passkey'):
    return pending_account(ticket,purpose)

@app.post('/api/passkey/register/options')
def passkey_register_options(p:PasskeyTicket):
    u=login_ticket_user(p.ticket,'passkey_setup')
    with db() as c:
        existing=c.execute('SELECT id FROM passkeys WHERE user_id=?',(u['id'],)).fetchall()
    opts=generate_registration_options(
        rp_id=RP_ID,rp_name='UniEvent',user_name=u['login'],user_display_name=u['name'],
        user_id=str(u['id']).encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r['id'])) for r in existing])
    cid=put_ceremony(u['id'],'register',opts.challenge)
    return {'ceremony':cid,'options':json.loads(options_to_json(opts))}

@app.post('/api/passkey/register/verify')
def passkey_register_verify(p:PasskeyProof):
    u=login_ticket_user(p.ticket,'passkey_setup')
    challenge=take_ceremony(p.ceremony,u['id'],'register')
    try:
        v=verify_registration_response(credential=p.credential,expected_challenge=challenge,
            expected_origin=ORIGIN,expected_rp_id=RP_ID,require_user_verification=True)
    except Exception:
        raise HTTPException(401,'Құрылғы растауы өтпеді')
    keyid=base64.urlsafe_b64encode(v.credential_id).decode().rstrip('=')
    pub=base64.urlsafe_b64encode(v.credential_public_key).decode().rstrip('=')
    with db() as c:
        try:
            c.execute('INSERT INTO passkeys(id,user_id,public_key,sign_count,created_at) VALUES(?,?,?,?,?)',
                      (keyid,u['id'],pub,v.sign_count,datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Бұл құрылғы бұрын тіркелген')
    # Include a passkey verification claim, not a TOTP claim.
    return {'token':token_for_passkey(u),'user':public_user(u)}

def token_for_passkey(u):
    return jwt.encode({'sub':str(u['id']),'mfa':True,'passkey':True,
        'exp':datetime.now(timezone.utc)+timedelta(hours=8)},SECRET,algorithm='HS256')

@app.post('/api/passkey/auth/options')
def passkey_auth_options(p:PasskeyTicket):
    u=login_ticket_user(p.ticket,'passkey')
    with db() as c:
        keys=c.execute('SELECT id FROM passkeys WHERE user_id=?',(u['id'],)).fetchall()
    if not keys:raise HTTPException(400,'Face ID әлі қосылмаған')
    opts=generate_authentication_options(rp_id=RP_ID,
         allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r['id'])) for r in keys],
         user_verification=UserVerificationRequirement.REQUIRED)
    cid=put_ceremony(u['id'],'authenticate',opts.challenge)
    return {'ceremony':cid,'options':json.loads(options_to_json(opts))}

@app.post('/api/passkey/auth/verify')
def passkey_auth_verify(p:PasskeyProof):
    u=login_ticket_user(p.ticket,'passkey')
    challenge=take_ceremony(p.ceremony,u['id'],'authenticate')
    with db() as c:
        cred=c.execute('SELECT * FROM passkeys WHERE id=? AND user_id=?',
                       (p.credential.get('id'),u['id'])).fetchone()
    if not cred:raise HTTPException(401,'Бұл құрылғы тіркелмеген')
    try:
        v=verify_authentication_response(credential=p.credential,expected_challenge=challenge,
            expected_origin=ORIGIN,expected_rp_id=RP_ID,
            credential_public_key=base64url_to_bytes(cred['public_key']),
            credential_current_sign_count=cred['sign_count'],require_user_verification=True)
    except Exception:
        raise HTTPException(401,'Face ID растау сәтсіз аяқталды')
    with db() as c:
        c.execute('UPDATE passkeys SET sign_count=? WHERE id=?',
                  (v.new_sign_count,cred['id']))
    return {'token':token_for_passkey(u),'user':public_user(u)}

# MFA: short-lived pending token never authorizes protected routes.
def mfa_challenge(u, purpose):
    return jwt.encode({'sub':str(u['id']),'purpose':purpose,'exp':datetime.now(timezone.utc)+timedelta(minutes=5)},SECRET,algorithm='HS256')

def pending_account(ticket, purpose):
    try:
        payload=jwt.decode(ticket,SECRET,algorithms=['HS256'])
        if payload.get('purpose') != purpose: raise ValueError('purpose')
        ident=int(payload['sub'])
    except (jwt.PyJWTError,ValueError,KeyError):
        raise HTTPException(401,'Код сұрауының мерзімі өтті. Қайта кіріңіз.')
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
    if not u: raise HTTPException(401,'Аккаунт табылмады')
    return dict(u)

class TicketIn(BaseModel):
    ticket: str
class CodeIn(BaseModel):
    ticket: str
    code: str = Field(pattern=r'^\d{6}$')

def valid_totp(secret, code, last_step=-1):
    current=int(time.time())//30
    totp=pyotp.TOTP(secret)
    for step in (current-1,current,current+1):
        if step>int(last_step or -1) and hmac.compare_digest(totp.at(step*30),code):
            return step
    return None

@app.post('/api/login')
def login(payload: Login):
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE login=?',(payload.login.strip(),)).fetchone()
    if not u or not verify_password(payload.password,u['password_hash']):
        raise HTTPException(401,'Логин немесе пароль қате')
    with db() as c:
        has_passkey=c.execute('SELECT 1 FROM passkeys WHERE user_id=? LIMIT 1',(u['id'],)).fetchone()
        has_face=c.execute('SELECT 1 FROM face_ids WHERE user_id=? LIMIT 1',(u['id'],)).fetchone()
    # First login is password-only until this user actively enables a second factor.
    # When both are enabled, let the owner choose which one to use for this login.
    if u['mfa_secret'] or has_passkey or has_face:
        return {
            'security_choice': True,
            'mfa_required': bool(u['mfa_secret']),
            'passkey_required': bool(has_passkey),
            'face_required': bool(has_face),
            'mfa_ticket': mfa_challenge(u,'verify') if u['mfa_secret'] else None,
            'passkey_ticket': mfa_challenge(u,'passkey') if has_passkey else None,
            'face_ticket': mfa_challenge(u,'face') if has_face else None,
        }
    return {'token':token_for(u),'user':public_user(u)}

@app.post('/api/mfa/setup')
def mfa_setup(payload:TicketIn):
    u=pending_account(payload.ticket,'setup')
    if u['mfa_secret']: raise HTTPException(409,'2FA әлдеқашан қосылған')
    secret=pyotp.random_base32()
    with db() as c:
        c.execute('UPDATE users SET mfa_pending=? WHERE id=? AND mfa_secret IS NULL',(secret,u['id']))
    uri=pyotp.TOTP(secret).provisioning_uri(name=u['login'],issuer_name='UniEvent')
    qr=qrcode.QRCode(box_size=5,border=2);qr.add_data(uri);qr.make(fit=True)
    buf=io.BytesIO();qr.make_image(fill_color='black',back_color='white').save(buf,format='PNG')
    return {'secret':secret,'qr':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}

@app.post('/api/mfa/confirm')
def mfa_confirm(payload:CodeIn):
    u=pending_account(payload.ticket,'setup')
    if u['mfa_secret'] or not u['mfa_pending']:raise HTTPException(400,'2FA баптауы табылмады')
    step=valid_totp(u['mfa_pending'],payload.code)
    if step is None:raise HTTPException(401,'6 таңбалы код дұрыс емес')
    with db() as c:
        changed=c.execute('UPDATE users SET mfa_secret=mfa_pending,mfa_pending=NULL,mfa_last_step=? WHERE id=? AND mfa_secret IS NULL AND mfa_pending=?',
                          (step,u['id'],u['mfa_pending'])).rowcount
    if not changed:raise HTTPException(409,'Қайта кіріңіз')
    with db() as c:
        fresh=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    return {'token':token_for(fresh, second_factor=True),'user':public_user(fresh)}

@app.post('/api/mfa/verify')
def mfa_verify(payload:CodeIn):
    u=pending_account(payload.ticket,'verify')
    if not u['mfa_secret']:raise HTTPException(400,'2FA орнатылмаған')
    step=valid_totp(u['mfa_secret'],payload.code,u['mfa_last_step'])
    if step is None: raise HTTPException(401,'Код қате немесе қолданылған')
    with db() as c:
        changed=c.execute('UPDATE users SET mfa_last_step=? WHERE id=? AND mfa_last_step<?',
                          (step,u['id'],step)).rowcount
    if not changed: raise HTTPException(401,'Код қолданылған')
    return {'token':token_for(u, second_factor=True),'user':public_user(u)}

@app.post('/api/passkey/enable-request')
def passkey_enable_request(u=Depends(require_user)):
    return {'passkey_setup':True,'ticket':mfa_challenge(u,'passkey_setup')}

@app.post('/api/mfa/enable-request')
def mfa_enable_request(u=Depends(require_user)):
    if u['mfa_secret']: raise HTTPException(409,'2FA қосылған')
    return {'setup_required':True,'ticket':mfa_challenge(u,'setup')}

# Camera Face ID: the browser computes a 128-float face descriptor (face-api.js)
# and the server only stores/compares descriptors. Password stays the first factor.
FACE_MATCH_DISTANCE = 0.6

class FaceDescriptorIn(BaseModel):
    ticket: str
    descriptor: list[float] = Field(min_length=128, max_length=128)

def clean_descriptor(values):
    if not all(math.isfinite(v) for v in values):
        raise HTTPException(422,'Бет дескрипторы жарамсыз')
    norm = math.sqrt(sum(v*v for v in values))
    if not 0.5 <= norm <= 2.0:
        raise HTTPException(422,'Бет дескрипторы жарамсыз')
    return [v/norm for v in values]

def token_for_face(u):
    return jwt.encode({'sub':str(u['id']),'mfa':True,'face':True,
        'exp':datetime.now(timezone.utc)+timedelta(hours=8)},SECRET,algorithm='HS256')

@app.post('/api/face/enable-request')
def face_enable_request(u=Depends(require_user)):
    return {'face_setup':True,'ticket':mfa_challenge(u,'face_setup')}

@app.post('/api/face/enroll')
def face_enroll(p:FaceDescriptorIn):
    u=pending_account(p.ticket,'face_setup')
    descriptor=clean_descriptor(p.descriptor)
    with db() as c:
        c.execute('''INSERT INTO face_ids(user_id,descriptor,created_at) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET descriptor=excluded.descriptor,created_at=excluded.created_at''',
            (u['id'],json.dumps(descriptor),datetime.now(timezone.utc).isoformat()))
    return {'token':token_for_face(u),'user':public_user(u)}

@app.post('/api/face/verify')
def face_verify(p:FaceDescriptorIn):
    u=pending_account(p.ticket,'face')
    with db() as c:
        row=c.execute('SELECT descriptor FROM face_ids WHERE user_id=?',(u['id'],)).fetchone()
    if not row: raise HTTPException(400,'Face ID әлі қосылмаған')
    stored=json.loads(row['descriptor'])
    probe=clean_descriptor(p.descriptor)
    distance=math.sqrt(sum((a-b)**2 for a,b in zip(stored,probe)))
    if distance > FACE_MATCH_DISTANCE:
        raise HTTPException(401,'Бет сәйкес келмеді. Қайта көріңіз.')
    return {'token':token_for_face(u),'user':public_user(u)}

@app.post('/api/face/disable')
def face_disable(u=Depends(require_user)):
    with db() as c:
        c.execute('DELETE FROM face_ids WHERE user_id=?',(u['id'],))
    return {'ok':True}

@app.get('/api/me')
def me(u=Depends(require_user)):
    return public_user(u)

@app.get('/api/security/status')
def security_status(u=Depends(require_user)):
    with db() as c:
        passkey_count=c.execute('SELECT COUNT(*) FROM passkeys WHERE user_id=?',(u['id'],)).fetchone()[0]
        face_enabled=bool(c.execute('SELECT 1 FROM face_ids WHERE user_id=? LIMIT 1',(u['id'],)).fetchone())
    return {'mfa_enabled': bool(u['mfa_secret']), 'passkey_enabled': passkey_count > 0, 'passkey_count': passkey_count, 'face_enabled': face_enabled}


def serialize_events(c, viewer):
    rows=c.execute('''SELECT e.*, u.name AS organizer_name FROM events e JOIN users u ON e.organizer_id=u.id ORDER BY e.date,e.time''').fetchall()
    answer=[]
    for e in rows:
        owner = viewer and (viewer['role']=='admin' or viewer['id']==e['organizer_id'])
        registered = c.execute('''SELECT u.name,u.login AS studentId,u.group_name AS "group" FROM registrations r JOIN users u ON u.id=r.student_id WHERE r.event_id=?''',(e['id'],)).fetchall()
        reviews = c.execute('''SELECT u.name,f.rating,f.text FROM feedbacks f JOIN users u ON u.id=f.user_id WHERE f.event_id=? ORDER BY f.id DESC''',(e['id'],)).fetchall()
        answer.append({'id':e['id'],'title':e['title'],'category':e['category'],'location':e['location'],
            'date':e['date'],'time':e['time'],'capacity':e['capacity'],
            'organizerId':f"ORG-{e['organizer_id']}",'organizerName':e['organizer_name'],
            'description':e['description'],'is_tournament':bool(e['is_tournament']),
            'registeredUsers':[dict(r) for r in registered] if owner else
                ([{'studentId':viewer['login'],'name':viewer['name'],'group':viewer['group_name']}] if viewer and any(r['studentId']==viewer['login'] for r in registered) else []) +
                [{'studentId':'', 'name':'Қатысушы','group':''} for r in registered if not viewer or r['studentId']!=viewer['login']],
            'feedbacks':[dict(r) for r in reviews]})
    return answer

@app.get('/api/events')
def get_events(user=Depends(require_user)):
    with db() as c:
        return serialize_events(c,user)

def notify(c, user_id, message):
    c.execute('INSERT INTO notifications(user_id,text,created_at) VALUES(?,?,?)',
              (user_id, message[:300], datetime.now(timezone.utc).isoformat(timespec='seconds')))

@app.get('/api/notifications')
def get_notifications(user=Depends(require_user)):
    with db() as c:
        rows=c.execute('SELECT id,text,created_at,read_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50',
                       (user['id'],)).fetchall()
    return [dict(row) for row in rows]

@app.post('/api/notifications/read')
def mark_notifications_read(user=Depends(require_user)):
    with db() as c:
        c.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL',
                  (datetime.now(timezone.utc).isoformat(timespec='seconds'),user['id']))
    return {'ok':True}

@app.get('/api/analytics')
def get_analytics(user=Depends(role_user('admin','organizer'))):
    with db() as c:
        if user['role']=='admin':
            scope=''; params=()
        else:
            scope=' WHERE e.organizer_id=?';params=(user['id'],)
        rows=c.execute('SELECT e.id,e.title,e.category,e.capacity,COUNT(r.student_id) AS registered FROM events e LEFT JOIN registrations r ON r.event_id=e.id'+scope+' GROUP BY e.id ORDER BY e.date',params).fetchall()
        event_ids=[r['id'] for r in rows]
        if event_ids:
            marks=','.join('?' for _ in event_ids)
            avg=c.execute(f'SELECT AVG(rating) FROM feedbacks WHERE event_id IN ({marks})',event_ids).fetchone()[0]
        else: avg=None
    return {'total_events':len(rows),'total_registrations':sum(r['registered'] for r in rows),
        'average_rating':round(avg,1) if avg else None,
        'fill_rate':round(100*sum(r['registered'] for r in rows)/sum(r['capacity'] for r in rows)) if rows else 0,
        'events':[dict(r) for r in rows]}

class TournamentIn(BaseModel):
    title: str = Field(min_length=2,max_length=150)
    game: str = Field(min_length=2,max_length=80)
    description: str = Field(default='',max_length=1000)

@app.get('/api/tournaments')
def get_tournaments(user=Depends(require_user)):
    with db() as c:
        rows=c.execute('SELECT t.*,u.name AS organizer_name FROM tournaments t JOIN users u ON u.id=t.organizer_id ORDER BY t.id DESC').fetchall()
    return [{'id':r['id'],'title':r['title'],'game':r['game'],'description':r['description'],
             'status':r['status'],'organizer':r['organizer_name'],
             'can_manage':user['role']=='admin' or user['id']==r['organizer_id']} for r in rows]

@app.post('/api/tournaments')
def create_tournament(payload:TournamentIn,user=Depends(role_user('organizer'))):
    with db() as c:
        cur=c.execute('INSERT INTO tournaments(title,game,description,organizer_id) VALUES(?,?,?,?)',
                      (payload.title,payload.game,payload.description,user['id']))
        for row in c.execute("SELECT id FROM users WHERE role='student'").fetchall():
            notify(c,row['id'],f'Жаңа турнир: {payload.title}')
    return {'id':cur.lastrowid}

@app.post('/api/tournaments/{tournament_id}/status')
def change_tournament_status(tournament_id:int, status:str, user=Depends(role_user('admin','organizer'))):
    if status not in {'Тіркелуде','Өтуде','Аяқталды'}:
        raise HTTPException(422,'Белгісіз мәртебе')
    with db() as c:
        t=c.execute('SELECT * FROM tournaments WHERE id=?',(tournament_id,)).fetchone()
        if not t: raise HTTPException(404,'Турнир табылмады')
        if user['role']!='admin' and t['organizer_id']!=user['id']:
            raise HTTPException(403,'Басқа ұйымдастырушының турнирі')
        c.execute('UPDATE tournaments SET status=? WHERE id=?',(status,tournament_id))
    return {'ok':True}

@app.post('/api/events')
def create_event(payload: EventIn, user=Depends(role_user('organizer'))):
    try:
        datetime.fromisoformat(f'{payload.date}T{payload.time}')
    except ValueError:
        raise HTTPException(422,'Күн немесе уақыт қате')
    with db() as c:
        cur=c.execute('''INSERT INTO events(title,category,location,date,time,capacity,description,organizer_id,is_tournament)
            VALUES(?,?,?,?,?,?,?,?,?)''',(payload.title,payload.category,payload.location,payload.date,payload.time,payload.capacity,payload.description,user['id'],int(payload.is_tournament)))
        for u in c.execute("SELECT id FROM users WHERE role='student'").fetchall():
            notify(c,u['id'],f'Жаңа шара: {payload.title}')
        return {'id':cur.lastrowid}

@app.delete('/api/events/{event_id}')
def delete_event(event_id:int,user=Depends(role_user('organizer','admin'))):
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        if not e: raise HTTPException(404,'Шара табылмады')
        if user['role']!='admin' and e['organizer_id']!=user['id']: raise HTTPException(403,'Тек өз шараңызды өшіре аласыз')
        c.execute('DELETE FROM events WHERE id=?',(event_id,))
    return {'ok':True}

@app.post('/api/events/{event_id}/register')
def register(event_id:int, payload:RegistrationIn, user=Depends(role_user('student'))):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        e=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        if not e: raise HTTPException(404,'Шара табылмады')
        if datetime.now() >= datetime.fromisoformat(f"{e['date']}T{e['time']}"):
            raise HTTPException(400,'Шараға тіркелу аяқталды')
        n=c.execute('SELECT COUNT(*) FROM registrations WHERE event_id=?',(event_id,)).fetchone()[0]
        if n>=e['capacity']: raise HTTPException(409,'Орындар толық')
        try:
            c.execute('UPDATE users SET group_name=? WHERE id=?', (payload.group.strip(), user['id']))
            c.execute('INSERT INTO registrations VALUES(?,?)',(event_id,user['id']))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Сіз бұрын тіркелгенсіз')
        notify(c,user['id'],f'Сіз «{e["title"]}» шарасына тіркелдіңіз')
    return {'ok':True}

@app.delete('/api/events/{event_id}/register')
def cancel(event_id:int,user=Depends(role_user('student'))):
    with db() as c:
        c.execute('DELETE FROM registrations WHERE event_id=? AND student_id=?',(event_id,user['id']))
    return {'ok':True}

@app.post('/api/events/{event_id}/feedback')
def feedback(event_id:int,payload:FeedbackIn,user=Depends(require_user)):
    with db() as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(event_id,)).fetchone(): raise HTTPException(404,'Шара табылмады')
        c.execute('INSERT INTO feedbacks(event_id,user_id,rating,text) VALUES(?,?,?,?)',(event_id,user['id'],payload.rating,payload.text))
    return {'ok':True}

ALLOWED = {'.pdf','.docx','.xlsx','.txt','.csv'}
MAX_BYTES = 5 * 1024 * 1024

@app.post('/api/documents')
async def upload_document(title:str=Form(...), file:UploadFile=File(...),user=Depends(role_user('organizer'))):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ALLOWED: raise HTTPException(400,'PDF, DOCX, XLSX, TXT немесе CSV керек')
    contents=await file.read(MAX_BYTES+1)
    if len(contents)>MAX_BYTES: raise HTTPException(413,'Максимум 5 МБ')
    stored=uuid.uuid4().hex+suffix
    (UPLOADS/stored).write_bytes(contents)
    with db() as c:
        cur=c.execute('INSERT INTO documents(title,filename,stored_name,sender_id,sent_date) VALUES(?,?,?,?,?)',
            (title[:150],Path(file.filename).name,stored,user['id'],datetime.now().strftime('%Y-%m-%d %H:%M')))
        for admin in c.execute("SELECT id FROM users WHERE role='admin'").fetchall():
            notify(c,admin['id'],f'Жаңа құжат: {title[:100]}')
    return {'id':cur.lastrowid}

@app.get('/api/documents')
def documents(user=Depends(role_user('admin'))):
    with db() as c:
        rows=c.execute('''SELECT d.*,u.name AS senderName FROM documents d JOIN users u ON u.id=d.sender_id ORDER BY d.id DESC''').fetchall()
    return [{'id':r['id'],'title':r['title'],'fileName':r['filename'],'senderName':r['senderName'],'sentDate':r['sent_date']} for r in rows]

@app.get('/api/documents/{doc_id}/download')
def download_document(doc_id:int,user=Depends(role_user('admin'))):
    with db() as c:
        row=c.execute('SELECT * FROM documents WHERE id=?',(doc_id,)).fetchone()
    if not row: raise HTTPException(404,'Файл табылмады')
    return FileResponse(UPLOADS/row['stored_name'],filename=row['filename'])

app.mount('/',StaticFiles(directory=str(FRONTEND),html=True),name='frontend')
