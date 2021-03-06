import json
import os
from http.cookies import SimpleCookie

import pytest

from urllib.parse import parse_qs, urlparse

from cryptojwt.jwt import utc_time_sans_frac
from cryptojwt.key_jar import build_keyjar

from oidcmsg.oauth2 import ResponseMessage
from oidcmsg.oidc import AuthorizationRequest
from oidcmsg.time_util import in_a_while

from oidcendpoint.endpoint_context import EndpointContext
from oidcendpoint.oidc.authorization import Authorization
from oidcendpoint.oidc.provider_config import ProviderConfiguration
from oidcendpoint.oidc.registration import Registration
from oidcendpoint.oidc.token import AccessToken
from oidcendpoint.oidc import userinfo
from oidcendpoint.user_authn.authn_context import INTERNETPROTOCOLPASSWORD
from oidcendpoint.user_info import UserInfo

KEYDEFS = [
    {"type": "RSA", "key": '', "use": ["sig"]},
    {"type": "EC", "crv": "P-256", "use": ["sig"]}
    ]

KEYJAR = build_keyjar(KEYDEFS)

RESPONSE_TYPES_SUPPORTED = [
    ["code"], ["token"], ["id_token"], ["code", "token"], ["code", "id_token"],
    ["id_token", "token"], ["code", "token", "id_token"], ['none']]

CAPABILITIES = {
    "response_types_supported": [" ".join(x) for x in RESPONSE_TYPES_SUPPORTED],
    "token_endpoint_auth_methods_supported": [
        "client_secret_post", "client_secret_basic",
        "client_secret_jwt", "private_key_jwt"],
    "response_modes_supported": ['query', 'fragment', 'form_post'],
    "subject_types_supported": ["public", "pairwise"],
    "grant_types_supported": [
        "authorization_code", "implicit",
        "urn:ietf:params:oauth:grant-type:jwt-bearer", "refresh_token"],
    "claim_types_supported": ["normal", "aggregated", "distributed"],
    "claims_parameter_supported": True,
    "request_parameter_supported": True,
    "request_uri_parameter_supported": True,
    }

AUTH_REQ = AuthorizationRequest(client_id='client_1',
                                redirect_uri='https://example.com/cb',
                                scope=['openid'],
                                state='STATE',
                                response_type='code')

AUTH_REQ_DICT = AUTH_REQ.to_dict()

BASEDIR = os.path.abspath(os.path.dirname(__file__))


def full_path(local_file):
    return os.path.join(BASEDIR, local_file)


USERINFO_db = json.loads(open(full_path('users.json')).read())


class SimpleCookieDealer(object):
    def __init__(self, name=''):
        self.name = name

    def create_cookie(self, value, typ, **kwargs):
        cookie = SimpleCookie()
        timestamp = str(utc_time_sans_frac())

        _payload = "::".join([value, timestamp, typ])

        bytes_load = _payload.encode("utf-8")
        bytes_timestamp = timestamp.encode("utf-8")

        cookie_payload = [bytes_load, bytes_timestamp]
        cookie[self.name] = (b"|".join(cookie_payload)).decode('utf-8')
        try:
            ttl = kwargs['ttl']
        except KeyError:
            pass
        else:
            cookie[self.name]["expires"] = in_a_while(seconds=ttl)

        return cookie

    @staticmethod
    def get_cookie_value(cookie=None, cookie_name=None):
        if cookie is None or cookie_name is None:
            return None
        else:
            try:
                info, timestamp = cookie[cookie_name].split('|')
            except (TypeError, AssertionError):
                return None
            else:
                value = info.split("::")
                if timestamp == value[1]:
                    return value
        return None


class TestEndpoint(object):
    @pytest.fixture(autouse=True)
    def create_endpoint(self):
        conf = {
            "issuer": "https://example.com/",
            "password": "mycket hemligt zebra",
            "token_expires_in": 600,
            "grant_expires_in": 300,
            "refresh_token_expires_in": 86400,
            "verify_ssl": False,
            "capabilities": CAPABILITIES,
            "jwks": {
                'url_path': '{}/jwks.json',
                'local_path': 'static/jwks.json',
                'private_path': 'own/jwks.json'
                },
            'endpoint': {
                'provider_config': {
                    'path': '{}/.well-known/openid-configuration',
                    'class': ProviderConfiguration,
                    'kwargs': {}
                    },
                'registration': {
                    'path': '{}/registration',
                    'class': Registration,
                    'kwargs': {}
                    },
                'authorization': {
                    'path': '{}/authorization',
                    'class': Authorization,
                    'kwargs': {}
                    },
                'token': {
                    'path': '{}/token',
                    'class': AccessToken,
                    'kwargs': {}
                    },
                'userinfo': {
                    'path': '{}/userinfo',
                    'class': userinfo.UserInfo,
                    'kwargs': {'db_file': 'users.json'}
                    }
                },
            "authentication": [{
                'acr': INTERNETPROTOCOLPASSWORD,
                'name': 'NoAuthn',
                'kwargs': {'user': 'diana'}
                }],
            "userinfo": {
                'class': UserInfo,
                'kwargs': {'db': USERINFO_db}
                },
            'template_dir': 'template'
            }
        endpoint_context = EndpointContext(conf, keyjar=KEYJAR,
                                           cookie_dealer=SimpleCookieDealer(
                                               'foo'))
        endpoint_context.cdb['client_1'] = {
            "client_secret": 'hemligt',
            "redirect_uris": [("https://example.com/cb", None)],
            "client_salt": "salted",
            'token_endpoint_auth_method': 'client_secret_post',
            'response_types': ['code', 'token', 'code id_token', 'id_token']
            }
        self.endpoint = Authorization(endpoint_context)

    def test_init(self):
        assert self.endpoint

    def test_parse(self):
        _req = self.endpoint.parse_request(AUTH_REQ_DICT)

        assert isinstance(_req, AuthorizationRequest)
        assert set(_req.keys()) == set(AUTH_REQ.keys())

    def test_process_request(self):
        _req = self.endpoint.parse_request(AUTH_REQ_DICT)
        _resp = self.endpoint.process_request(request=_req)
        assert set(_resp.keys()) == {'response_args', 'fragment_enc',
                                     'return_uri', 'cookie'}

    def test_do_response_code(self):
        _req = self.endpoint.parse_request(AUTH_REQ_DICT)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        _msg = parse_qs(msg['response'])
        assert _msg
        part = urlparse(msg['response'])
        assert part.fragment == ''
        assert part.query
        _query = parse_qs(part.query)
        assert _query
        assert 'code' in _query

    def test_do_response_id_token_no_nonce(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'id_token'
        _req = self.endpoint.parse_request(_orig_req)
        # Missing nonce
        assert isinstance(_req, ResponseMessage)

    def test_do_response_id_token(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'id_token'
        _orig_req['nonce'] = 'rnd_nonce'
        _req = self.endpoint.parse_request(_orig_req)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        part = urlparse(msg['response'])
        assert part.query == ''
        assert part.fragment
        _frag_msg = parse_qs(part.fragment)
        assert _frag_msg
        assert 'id_token' in _frag_msg
        assert 'code' not in _frag_msg
        assert 'token' not in _frag_msg

    def test_do_response_id_token_token(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'id_token token'
        _orig_req['nonce'] = 'rnd_nonce'
        _req = self.endpoint.parse_request(_orig_req)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        part = urlparse(msg['response'])
        assert part.query == ''
        assert part.fragment
        _frag_msg = parse_qs(part.fragment)
        assert _frag_msg
        assert 'id_token' in _frag_msg
        assert 'code' not in _frag_msg
        assert 'access_token' in _frag_msg

    def test_do_response_code_token(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'code token'
        _req = self.endpoint.parse_request(_orig_req)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        part = urlparse(msg['response'])
        assert part.query == ''
        assert part.fragment
        _frag_msg = parse_qs(part.fragment)
        assert _frag_msg
        assert 'id_token' not in _frag_msg
        assert 'code' in _frag_msg
        assert 'access_token' in _frag_msg

    def test_do_response_code_id_token(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'code id_token'
        _orig_req['nonce'] = 'rnd_nonce'
        _req = self.endpoint.parse_request(_orig_req)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        part = urlparse(msg['response'])
        assert part.query == ''
        assert part.fragment
        _frag_msg = parse_qs(part.fragment)
        assert _frag_msg
        assert 'id_token' in _frag_msg
        assert 'code' in _frag_msg
        assert 'access_token' not in _frag_msg

    def test_do_response_code_id_token_token(self):
        _orig_req = AUTH_REQ_DICT.copy()
        _orig_req['response_type'] = 'code id_token token'
        _orig_req['nonce'] = 'rnd_nonce'
        _req = self.endpoint.parse_request(_orig_req)
        _resp = self.endpoint.process_request(request=_req)
        msg = self.endpoint.do_response(**_resp)
        assert isinstance(msg, dict)
        part = urlparse(msg['response'])
        assert part.query == ''
        assert part.fragment
        _frag_msg = parse_qs(part.fragment)
        assert _frag_msg
        assert 'id_token' in _frag_msg
        assert 'code' in _frag_msg
        assert 'access_token' in _frag_msg
