"""Minimal RFC 6238 TOTP helper for the UniEvent teaching example."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

def random_base32():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')

class TOTP:
    def __init__(self, secret): self.secret=secret
    def at(self, timestamp):
        step=int(float(timestamp)//30)
        key=base64.b32decode(self.secret.upper()+'='*((-len(self.secret))%8))
        digest=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest()
        offset=digest[-1]&15
        val=(struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000
        return f'{val:06d}'
    def now(self):return self.at(time.time())
    def provisioning_uri(self,name,issuer_name):
        label=quote(issuer_name+':'+name,safe='')
        return 'otpauth://totp/'+label+'?'+urlencode({'secret':self.secret,'issuer':issuer_name,'algorithm':'SHA1','digits':6,'period':30})
