import contextlib

import pytest
from flask import g

from api.teams.member_exec import MemberCommandError
from api.teams.member_identity import member_identity, resolve_member_team

from .conftest import CONFIG, seed_record

ACCESS = {"sub": "sub-1", "username": "uuid-1", "cognito:groups": ["member-teama"]}
ID = {"sub": "sub-1", "token_use": "id", "email": "Carol@example.com", "email_verified": True}


@pytest.fixture
def ctx(app, mocker, parameter_store):
    seed_record(parameter_store, "teama", ad_group="teamA", fsx_path="/fsx/teamA")
    seed_record(parameter_store, "teamb", ad_group="teamB", fsx_path="/fsx/teamB")
    decode = mocker.patch("api.teams.member_identity.jwt_decode")

    @contextlib.contextmanager
    def _make(access=None, id_claims=None, cookies=None):
        decode.return_value = dict(ID, **(id_claims or {}))
        cookies = cookies or {"idToken": "id-token", "accessToken": "access-token"}
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        with app.test_request_context("/team-quota/member/me?user=bob", headers={"Cookie": cookie_header},
                                      json={"user": "bob", "email": "bob@example.com"}):
            g.pcui_user_claims = dict(ACCESS, **(access or {}))
            yield decode

    return _make


def test_verified_email_maps_to_prefix(ctx):
    with ctx() as decode:
        identity = member_identity(CONFIG)
        assert (identity["email"], identity["user"], identity["teams"]) == ("carol@example.com", "carol", ["teama"])
        assert identity["records"]["teama"]["fsx_path"] == "/fsx/teamA"
        assert decode.call_args.args[0] == "id-token"
        assert decode.call_args.kwargs["access_token"] == "access-token"


def test_request_input_has_no_effect(ctx):
    with ctx():
        assert member_identity(CONFIG)["user"] == "carol"  # ?user=bob and the body are ignored


@pytest.mark.parametrize("id_claims,access", [
    ({"email_verified": False}, None),
    ({"email_verified": "false"}, None),
    ({"sub": "sub-2"}, None),
    ({"token_use": "access"}, None),
    ({"email": "carol@other.com"}, None),
    ({"email": "1abc@example.com"}, None),
    ({"email": "a" * 21 + "@example.com"}, None),
    ({"email": "root@example.com"}, None),
    ({"email": None}, None),
    (None, {"cognito:groups": ["team-teama"]}),
    (None, {"cognito:groups": ["member-teamz"]}),
    (None, {"cognito:groups": ["member-TeamA", "member-root"]}),
])
def test_rejections_are_403(ctx, id_claims, access):
    with ctx(access=access, id_claims=id_claims):
        with pytest.raises(MemberCommandError) as err:
            member_identity(CONFIG)
        assert err.value.status == 403


def test_missing_id_token_is_403(ctx):
    with ctx(cookies={"accessToken": "access-token"}):
        with pytest.raises(MemberCommandError) as err:
            member_identity(CONFIG)
        assert err.value.status == 403


def test_invalid_id_token_is_403(ctx):
    with ctx() as decode:
        decode.side_effect = Exception("bad signature")
        with pytest.raises(MemberCommandError):
            member_identity(CONFIG)


def test_refreshed_tokens_are_used(ctx):
    with ctx() as decode:
        g.auth_cookies = {"idToken": "new-id", "accessToken": "new-access"}
        member_identity(CONFIG)
        assert decode.call_args.args[0] == "new-id"
        assert decode.call_args.kwargs["access_token"] == "new-access"


def test_inactive_record_is_not_a_team(ctx, parameter_store):
    seed_record(parameter_store, "teama", state="provisioning")
    with ctx():
        with pytest.raises(MemberCommandError):
            member_identity(CONFIG)


def test_resolve_member_team(ctx):
    with ctx(access={"cognito:groups": ["member-teama", "member-teamb"]}):
        identity = member_identity(CONFIG)
        assert identity["teams"] == ["teama", "teamb"]
        assert resolve_member_team(identity, "teamb") == "teamb"
        with pytest.raises(MemberCommandError) as err:
            resolve_member_team(identity, "teamc")
        assert err.value.status == 403
        with pytest.raises(MemberCommandError) as err:
            resolve_member_team(identity)
        assert err.value.status == 400
        single = {"teams": ["teama"]}
        assert resolve_member_team(single) == "teama"
