import time
import pytest

from oidcendpoint import token_handler
from oidcendpoint.authn_event import create_authn_event
from oidcendpoint.in_memory_db import InMemoryDataBase
from oidcendpoint.session import SessionDB
from oidcendpoint.sso_db import SSODb
from oidcendpoint.token_handler import AccessCodeUsed
from oidcendpoint.token_handler import ExpiredToken
from oidcendpoint.token_handler import WrongTokenType

from oidcmsg.oidc import AuthorizationRequest
from oidcmsg.oidc import OpenIDRequest


__author__ = 'rohe0002'

AREQ = AuthorizationRequest(response_type="code", client_id="client1",
                            redirect_uri="http://example.com/authz",
                            scope=["openid"], state="state000")

AREQN = AuthorizationRequest(response_type="code", client_id="client1",
                             redirect_uri="http://example.com/authz",
                             scope=["openid"], state="state000",
                             nonce="something")

AREQO = AuthorizationRequest(response_type="code", client_id="client1",
                             redirect_uri="http://example.com/authz",
                             scope=["openid", "offline_access"],
                             prompt="consent", state="state000")

OIDR = OpenIDRequest(response_type="code", client_id="client1",
                     redirect_uri="http://example.com/authz", scope=["openid"],
                     state="state000")


class TestSessionDB(object):
    @pytest.fixture(autouse=True)
    def create_sdb(self):
        _sso_db = SSODb()
        _token_handler = token_handler.factory('losenord')
        self.sdb = SessionDB(InMemoryDataBase(), _token_handler, _sso_db)

    def test_create_authz_session(self):
        ae = create_authn_event("uid", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        self.sdb.do_sub(sid, "client_salt")

        info = self.sdb[sid]
        assert info["client_id"] == "client_id"
        assert set(info.keys()) == {'client_id', 'authn_req', 'authn_event',
                                    'sub', 'oauth_state', 'code'}

    def test_create_authz_session_without_nonce(self):
        ae = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        info = self.sdb[sid]
        assert info["oauth_state"] == "authz"

    def test_create_authz_session_with_nonce(self):
        ae = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae, AREQN, client_id='client_id')
        info = self.sdb[sid]
        authz_request = info['authn_req']
        assert authz_request["nonce"] == "something"

    def test_create_authz_session_with_id_token(self):
        ae = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id',
                                            id_token="id_token")

        info = self.sdb[sid]
        assert info["id_token"] == "id_token"

    def test_create_authz_session_with_oidreq(self):
        ae = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id',
                                            oidreq=OIDR)
        info = self.sdb[sid]
        assert "id_token" not in info
        assert "oidreq" in info

    def test_create_authz_session_with_sector_id(self):
        ae = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id',
                                            oidreq=OIDR)
        self.sdb.do_sub(sid, "client_salt", "http://example.com/si.jwt",
                        "pairwise")

        info_1 = self.sdb[sid].copy()
        assert "id_token" not in info_1
        assert "oidreq" in info_1
        assert info_1["sub"] != "sub"

        self.sdb.do_sub(sid, "client_salt", "http://example.net/si.jwt",
                        "pairwise")

        info_2 = self.sdb[sid]
        assert info_2["sub"] != "sub"
        assert info_2["sub"] != info_1["sub"]

    def test_upgrade_to_token(self):
        ae1 = create_authn_event("uid", "salt")
        sid = self.sdb.create_authz_session(ae1, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'
        grant = self.sdb[sid]["code"]
        _dict = self.sdb.upgrade_to_token(grant)

        print(_dict.keys())
        assert set(_dict.keys()) == {
            'authn_event', 'code', 'authn_req', 'access_token', 'token_type',
            'client_id', 'oauth_state', 'expires_in'}

        # can't update again
        with pytest.raises(AccessCodeUsed):
            self.sdb.upgrade_to_token(grant)
            self.sdb.upgrade_to_token(_dict["access_token"])

    def test_upgrade_to_token_refresh(self):
        ae1 = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae1, AREQO, client_id='client_id')
        self.sdb.do_sub(sid, ae1['salt'])
        grant = self.sdb[sid]["code"]
        # Issue an access token trading in the access grant code
        _dict = self.sdb.upgrade_to_token(grant, issue_refresh=True)

        print(_dict.keys())
        assert set(_dict.keys()) == {
            'authn_event', 'code', 'authn_req', 'access_token', 'sub',
            'token_type', 'client_id', 'oauth_state', 'refresh_token',
            'expires_in'}

        # can't get another access token using the same code
        with pytest.raises(AccessCodeUsed):
            self.sdb.upgrade_to_token(grant)

        # You can't refresh a token using the token itself
        with pytest.raises(WrongTokenType):
            self.sdb.refresh_token(_dict["access_token"])

        # If the code has been used twice then the refresh token should not work
        with pytest.raises(ExpiredToken):
            self.sdb.refresh_token(_dict["refresh_token"])

    def test_upgrade_to_token_with_id_token_and_oidreq(self):
        ae2 = create_authn_event("another_user_id", "salt")
        sid = self.sdb.create_authz_session(ae2, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'
        grant = self.sdb[sid]["code"]

        _dict = self.sdb.upgrade_to_token(grant, id_token="id_token",
                                          oidreq=OIDR)
        print(_dict.keys())
        assert set(_dict.keys()) == {
            'authn_event', 'code', 'authn_req', 'oidreq', 'access_token',
            'id_token', 'token_type', 'client_id', 'oauth_state', 'expires_in'}

        assert _dict["id_token"] == "id_token"
        assert isinstance(_dict["oidreq"], OpenIDRequest)

    def test_refresh_token(self):
        ae = create_authn_event("uid", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'
        grant = self.sdb[sid]["code"]

        dict1 = self.sdb.upgrade_to_token(grant, issue_refresh=True).copy()
        rtoken = dict1["refresh_token"]
        dict2 = self.sdb.refresh_token(rtoken, AREQ['client_id'])

        assert dict1["access_token"] != dict2["access_token"]

        with pytest.raises(WrongTokenType):
            self.sdb.refresh_token(dict2["access_token"], AREQ['client_id'])

    def test_refresh_token_cleared_session(self):
        ae = create_authn_event('uid', 'salt')
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'
        grant = self.sdb[sid]['code']
        dict1 = self.sdb.upgrade_to_token(grant, issue_refresh=True)
        ac1 = dict1['access_token']

        # Purge the SessionDB
        self.sdb._db = {}

        rtoken = dict1['refresh_token']
        with pytest.raises(KeyError):
            self.sdb.refresh_token(rtoken, AREQ['client_id'])

    def test_is_valid(self):
        ae1 = create_authn_event("uid", "salt")
        sid = self.sdb.create_authz_session(ae1, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'
        grant = self.sdb[sid]["code"]

        assert self.sdb.is_valid(grant)

        sinfo = self.sdb.upgrade_to_token(grant, issue_refresh=True)
        assert not self.sdb.is_valid(grant)
        access_token = sinfo["access_token"]
        assert self.sdb.is_valid(access_token)

        refresh_token = sinfo["refresh_token"]
        sinfo = self.sdb.refresh_token(refresh_token, AREQ['client_id'])
        access_token2 = sinfo["access_token"]
        assert self.sdb.is_valid(access_token2)

        # The old access code should be invalid
        try:
            self.sdb.is_valid(access_token)
        except KeyError:
            pass

    def test_valid_grant(self):
        ae = create_authn_event("another:user", "salt")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        grant = self.sdb[sid]["code"]

        assert self.sdb.is_valid(grant)

    def test_revoke_token(self):
        ae1 = create_authn_event("uid", "salt")
        sid = self.sdb.create_authz_session(ae1, AREQ, client_id='client_id')
        self.sdb[sid]['sub'] = 'sub'

        grant = self.sdb[sid]["code"]
        tokens = self.sdb.upgrade_to_token(grant, issue_refresh=True)
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        assert self.sdb.is_valid(access_token)

        self.sdb.revoke_token(access_token)
        assert not self.sdb.is_valid(access_token)

        sinfo = self.sdb.refresh_token(refresh_token, AREQ['client_id'])
        access_token = sinfo["access_token"]
        assert self.sdb.is_valid(access_token)

        self.sdb.revoke_token(refresh_token)
        assert not self.sdb.is_valid(refresh_token)

        try:
            self.sdb.refresh_token(refresh_token, AREQ['client_id'])
        except ExpiredToken:
            pass

        assert self.sdb.is_valid(access_token)

        ae2 = create_authn_event("sub", "salt")
        sid = self.sdb.create_authz_session(ae2, AREQ, client_id='client_2')

        grant = self.sdb[sid]["code"]
        self.sdb.revoke_token(grant)
        assert not self.sdb.is_valid(grant)

    def test_sub_to_authn_event(self):
        ae = create_authn_event("sub", "salt", time_stamp=time.time())
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        sub = self.sdb.do_sub(sid, "client_salt")

        # given the sub find out whether the authn event is still valid
        sids = self.sdb.get_sids_by_sub(sub)
        ae = self.sdb[sids[0]]['authn_event']
        assert ae.valid()

    def test_do_sub_deterministic(self):
        ae = create_authn_event("tester", "random_value")
        sid = self.sdb.create_authz_session(ae, AREQ, client_id='client_id')
        self.sdb.do_sub(sid, "other_random_value")

        info = self.sdb[sid]
        assert info["sub"] == \
               '179670cdee6375c48e577317b2abd7d5cd26a5cdb1cfb7ef84af3d703c71d013'

        self.sdb.do_sub(sid, "other_random_value",
                        sector_id='http://example.com',
                        subject_type="pairwise")
        info2 = self.sdb[sid]
        assert info2["sub"] == \
               'aaa50d80f8780cf1c4beb39e8e126556292f5091b9e39596424fefa2b99d9c53'

        self.sdb.do_sub(sid, "another_random_value",
                        sector_id='http://other.example.com',
                        subject_type="pairwise")

        info2 = self.sdb[sid]
        assert info2["sub"] == \
               '62fb630e29f0d41b88e049ac0ef49a9c3ac5418c029d6e4f5417df7e9443976b'
