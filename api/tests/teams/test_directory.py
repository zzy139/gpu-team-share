import shlex

import pytest

from api.teams import directory
from api.teams.directory import AD_SCRIPT, ENV_OWNER_SCRIPT, TEAM_DIR_SCRIPT

from .conftest import CONFIG


def _argv(command):
    return shlex.split(command)


def test_ad_command_round_trips_and_carries_only_the_secret_arn():
    command = directory.ad_command(CONFIG, "teamc", ["create-group", "create-user:carol:carol@example.com", "add:carol"])
    argv = _argv(command)
    assert argv[:4] == ["bash", "-c", AD_SCRIPT, "pcui-ad"]
    assert argv[4:] == ["us-west-2", CONFIG["ad_admin_secret_arn"], "corp.example.internal", "10.0.0.10",
                        "Administrator", "teamc", "create-group:", "create-user:carol:carol@example.com", "add:carol"]


@pytest.mark.parametrize("group,ops", [
    ("teamc;id", ["create-group"]),
    ("team c", ["create-group"]),
    ("teamc", ["add:$(id)"]),
    ("teamc", ["add:carol;reboot"]),
    ("teamc", ["create-user:carol:bob@example.com"]),
    ("teamc", ["create-user:carol:carol@example.com;id"]),
    ("teamc", ["rm:carol"]),
    ("teamc", ["lookup:Carol"]),
    ("teamc", []),
])
def test_invalid_ad_input_is_rejected_before_building(group, ops):
    with pytest.raises(ValueError):
        directory.ad_command(CONFIG, group, ops)


def test_ad_script_never_exposes_the_password():
    assert "set -x" not in AD_SCRIPT
    assert 'echo "$pw"' not in AD_SCRIPT and "echo $pw" not in AD_SCRIPT
    assert "-y <(printf '%s' \"$pw\")" in AD_SCRIPT
    assert " -w " not in AD_SCRIPT
    assert "--stdin-password" in AD_SCRIPT
    assert "set -euo pipefail" in AD_SCRIPT


def test_team_dir_command_creates_the_team_and_envs_dirs():
    argv = _argv(directory.team_dir_command("/efs/teamC", "teamC", "teamc"))
    assert argv == ["bash", "-c", TEAM_DIR_SCRIPT, "pcui-team-dir", "/efs/teamC", "teamC", "teamc"]
    assert "chmod 2770" in TEAM_DIR_SCRIPT and "chmod 3770" in TEAM_DIR_SCRIPT and "sss_cache -E" in TEAM_DIR_SCRIPT
    assert 'mkdir -p -- "$dir/envs"' in TEAM_DIR_SCRIPT
    # the environment root /efs/envs (0755 root:root) and /efs/envs/<team> (2751 root:<group>)
    assert 'root="$(dirname -- "$dir")/envs"' in TEAM_DIR_SCRIPT
    assert 'chown root:root -- "$root"' in TEAM_DIR_SCRIPT and 'chmod 0755 -- "$root"' in TEAM_DIR_SCRIPT
    assert 'chgrp -- "$group" "$root/$team"' in TEAM_DIR_SCRIPT and 'chmod 2751 -- "$root/$team"' in TEAM_DIR_SCRIPT
    assert '[ ! -L "$root/$team" ]' in TEAM_DIR_SCRIPT and "@@ENVTEAMDIR" in TEAM_DIR_SCRIPT
    for bad in ("/efs/../etc", "/efs//teamc", "/efs/teamc/", "/teamc", "efs/teamc"):
        with pytest.raises(ValueError):
            directory.team_dir_command(bad, "teamc", "teamc")
    with pytest.raises(ValueError):
        directory.team_dir_command("/efs/teamc", "teamc;id", "teamc")
    for team in ("../x", "Teamc", "envs", "shared", "a;id"):
        with pytest.raises(ValueError):
            directory.team_dir_command("/efs/teamc", "teamc", team)


def test_probe_sections():
    sections = directory.probe_sections(CONFIG, "teamA", ["alice"], "/efs/teamA", "teama")
    assert [name for name, _ in sections] == ["ADPROBE", "DIRPROBE"]
    assert sections[0][1][-3:] == ["probe-group:", "lookup:alice", "probe-member:alice"]
    assert sections[1][1][-2:] == ["/efs/teamA", "teama"]
    assert "@@ENVTEAMDIR" in sections[1][1][2]


def test_ad_disable_and_enable_ops():
    argv = _argv(directory.ad_command(CONFIG, "teamA", ["disable:alice", "enable:alice"]))
    assert argv[-2:] == ["disable:alice", "enable:alice"]
    assert "userAccountControl" in AD_SCRIPT and "514" in AD_SCRIPT and "512" in AD_SCRIPT
    assert "ldapmodify -x" in AD_SCRIPT and "-y <(printf '%s' \"$pw\")" in AD_SCRIPT
    with pytest.raises(ValueError):
        directory.ad_command(CONFIG, "teamA", ["disable:alice;id"])


def test_env_owner_command_hands_environments_to_the_lead():
    argv = _argv(directory.env_owner_command("/efs/envs/teama", "carol", "lead"))
    assert argv == ["bash", "-c", ENV_OWNER_SCRIPT, "pcui-env-owner", "/efs/envs/teama", "carol", "lead"]
    assert "chown -R -h -P --from=" in ENV_OWNER_SCRIPT and "O_NOFOLLOW" in ENV_OWNER_SCRIPT
    assert "-mindepth 2 -maxdepth 2" in ENV_OWNER_SCRIPT  # <name>/<version> below /efs/envs/<team>
    for bad in (("/efs/envs/teama", "carol;id", "lead"), ("/efs/envs/teama", "carol", "$(id)"),
                ("/efs/../x", "a", "b")):
        with pytest.raises(ValueError):
            directory.env_owner_command(*bad)
    assert directory.parse_env_owner("@@ENVOWNER 3") == 3
    assert directory.parse_env_owner("noise") is None


def test_parse_ad_output():
    parsed = directory.parse_ad_output([
        "@@AD group teamc created", "@@AD user carol absent", "@@AD user dave mail=Dave@Example.com",
        "@@AD user alice nomail", "@@AD user erin created", "@@AD member carol added", "noise",
    ])
    assert parsed["groups"] == {"teamc": "created"}
    assert parsed["users"]["carol"] == {"state": "absent", "mail": None}
    assert parsed["users"]["dave"] == {"state": "mail", "mail": "dave@example.com"}
    assert parsed["users"]["alice"]["state"] == "nomail"
    assert parsed["created"] == ["erin"]
    assert parsed["members"] == {"carol": "added"}
    assert directory.parse_ad_output("@@AD error 3")["error"] == "3"


def test_parse_team_dir_and_ready():
    env_team = "\n@@ENVTEAMDIR drwxr-s--x teamA"
    state = directory.parse_team_dir("@@TEAMDIR drwxrws--- teamA\n@@ENVDIR drwxrws--T teamA" + env_team)
    assert directory.team_dir_ready(state, "teamA")
    assert state["env_team"] == {"exists": True, "mode": "drwxr-s--x", "group": "teamA"}
    assert not directory.team_dir_ready(state, "teama")  # group names are case-sensitive
    no_sticky = directory.parse_team_dir("@@TEAMDIR drwxrws--- teamA\n@@ENVDIR drwxrws--- teamA" + env_team)
    assert not directory.team_dir_ready(no_sticky, "teamA")
    missing_envs = directory.parse_team_dir("@@TEAMDIR drwxrws--- teamA\n@@ENVDIR missing" + env_team)
    assert not directory.team_dir_ready(missing_envs, "teamA")
    assert missing_envs["envs"]["exists"] is False
    # an existing team without /efs/envs/<team> (created before the new layout) is not ready: storage runs again
    old_team = directory.parse_team_dir("@@TEAMDIR drwxrws--- teamA\n@@ENVDIR drwxrws--T teamA\n@@ENVTEAMDIR missing")
    assert not directory.team_dir_ready(old_team, "teamA")
    readable = directory.parse_team_dir("@@TEAMDIR drwxrws--- teamA\n@@ENVDIR drwxrws--T teamA\n"
                                        "@@ENVTEAMDIR drwxr-sr-x teamA")
    assert not directory.team_dir_ready(readable, "teamA")
    # SSSD lag: the lines are missing entirely
    assert directory.parse_team_dir("")["dir"]["exists"] is False
