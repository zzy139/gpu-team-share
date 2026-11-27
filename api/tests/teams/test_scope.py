import pytest
from flask import g
from werkzeug.exceptions import Forbidden

from api.teams.scope import caller_roles, caller_teams, filter_teams, is_admin, resolve_team_scope


def _with_groups(app, groups):
    ctx = app.test_request_context()
    ctx.push()
    g.pcui_user_claims = {"cognito:groups": groups, "username": "u"}
    return ctx


@pytest.fixture
def as_groups(app):
    contexts = []

    def _push(groups):
        contexts.append(_with_groups(app, groups))

    yield _push
    for ctx in reversed(contexts):
        ctx.pop()


def test_admin_sees_all_teams(as_groups):
    as_groups(["admin"])
    assert is_admin()
    assert resolve_team_scope(None) is None
    assert resolve_team_scope("teamb") == ["teamb"]


def test_lead_sees_only_own_team(as_groups):
    as_groups(["team-teama"])
    assert not is_admin()
    assert caller_teams() == ["teama"]
    assert resolve_team_scope(None) == ["teama"]
    assert resolve_team_scope("teama") == ["teama"]


def test_lead_asking_for_other_team_gets_403(as_groups):
    as_groups(["team-teama"])
    with pytest.raises(Forbidden):
        resolve_team_scope("teamb")


def test_multi_team_lead(as_groups):
    as_groups(["team-teamb", "team-teama"])
    assert resolve_team_scope(None) == ["teama", "teamb"]
    assert resolve_team_scope("teamb") == ["teamb"]


def test_caller_without_team_gets_403(as_groups):
    as_groups(["viewer"])
    with pytest.raises(Forbidden):
        resolve_team_scope(None)


@pytest.mark.parametrize("group", ["team-", "team-root", "team-TeamA", "team-a;id", "team-borrow"])
def test_invalid_team_groups_are_ignored(as_groups, group):
    as_groups([group])
    assert caller_teams() == []
    with pytest.raises(Forbidden):
        resolve_team_scope(None)


def test_missing_claims_has_no_roles(app):
    with app.test_request_context():
        assert caller_roles() == []
        with pytest.raises(Forbidden):
            resolve_team_scope(None)


def test_non_list_roles_claim_is_ignored(app):
    with app.test_request_context():
        g.pcui_user_claims = {"cognito:groups": "admin"}
        assert not is_admin()


def test_disabled_auth_acts_as_admin(app, mock_disable_auth):
    with app.test_request_context():
        assert is_admin()


def test_filter_teams():
    rows = [{"name": "teama"}, {"name": "teamb"}]
    assert filter_teams(rows, None) == rows
    assert filter_teams(rows, ["teama"]) == [{"name": "teama"}]
    assert filter_teams(rows, []) == []
