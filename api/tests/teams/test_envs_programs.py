"""The environment programs (member: login node, root: head node) run in-process against a temporary tree.

The programs are constants executed with ``python3``; here they are loaded into a namespace and their ``main`` is
called with argv, so path checks, the status model, the visibility modes and the symbolic-link defences run for
real. ``sbatch`` and ``scancel`` are replaced, root ownership changes are no-ops (tests do not run as root: the test
user plays root, so "root-owned" means owned by the test user) and background work runs inline. The set-group-id
bit is not asserted: macOS clears it for a non-root caller outside the directory's group.
"""
import base64
import gzip
import json
import os
import pwd
import subprocess

import pytest

from api.teams import envs

ME = pwd.getpwuid(os.getuid()).pw_name


def _load(program):
    namespace = {"__name__": "pcui_env_program"}
    exec(compile(program, "<env-program>", "exec"), namespace)
    return namespace


def _token(data):
    return base64.b64encode(gzip.compress(data if isinstance(data, bytes) else data.encode())).decode()


def _meta(**extra):
    meta = {"kind": "container", "source": "image", "builder": "slurm", "image": "nvcr.io/nvidia/cuda:12.4.1",
            "ref": "docker://nvcr.io#nvidia/cuda:12.4.1", "status": "queued", "owner": "someone-else",
            "description": "CUDA $(reboot); `id` | rm -rf /", "attempt": 1}
    meta.update(extra)
    return _token(json.dumps(meta))


def _mode(path):
    return oct(os.stat(path, follow_symlinks=False).st_mode & 0o1777)


class Sbatch:
    def __init__(self, returncode=0, stdout="4242\n", stderr=""):
        self.calls = []
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] in ("sbatch", "scancel", "rm"):
            return self.result if argv[0] == "sbatch" else subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command {argv}")


@pytest.fixture
def tree(tmp_path, mocker, capsys):
    mount = tmp_path / "efs"
    envs_root = mount / "envs"
    for team in ("teama", "teamb"):
        (envs_root / team).mkdir(parents=True)
        os.chmod(envs_root / team, 0o2751)
        (mount / team).mkdir()  # the team directory (/efs/<team>): its group is the team's group
    (envs_root / "_public").mkdir()
    tools = mount / "shared" / "tools"
    tools.mkdir(parents=True)
    builder = tools / "hpc-env-build"
    builder.write_text("#!/bin/bash\n")
    builder.chmod(0o755)
    member, root = _load(envs.MEMBER_ENV_PY), _load(envs.ENV_ROOT_PY)
    root["background"] = lambda work: work()
    sbatch = Sbatch()
    mocker.patch.object(subprocess, "run", side_effect=sbatch)
    fchown = mocker.patch("os.fchown")
    mocker.patch("os.chown")

    def run(program, *argv):
        try:
            code = program["run"](program["main"], [str(a) for a in argv])
        except SystemExit as exit_:
            code = exit_.code
        captured = capsys.readouterr()
        return code, envs.markers(captured.out), captured.err

    yield {"mount": mount, "envs": envs_root, "team": envs_root / "teama", "shared": mount / "shared",
           "member": lambda *a: run(member, *a), "root": lambda *a: run(root, *a), "sbatch": sbatch,
           "fchown": fchown}
    for dirpath, dirnames, _ in os.walk(tmp_path):  # undo chmod 0 of the "unreadable" cases
        for d in dirnames:
            if not os.path.islink(os.path.join(dirpath, d)):
                os.chmod(os.path.join(dirpath, d), 0o755)


def _prepare(tree, name="cuda", visibility="team", team="teama"):
    return tree["root"]("prepare", tree["envs"], tree["mount"] / team, team, name, visibility)


def _create(tree, name="cuda", version="12.4", builder="slurm", spec_name="-", spec="-", cap=0, meta=None,
            prepare=True):
    if prepare:
        _prepare(tree, name)
    return tree["member"]("env-create", tree["envs"], tree["shared"], "teama", name, version, meta or _meta(),
                          spec_name, spec, cap, builder)


def _env_json(path):
    return json.loads((path / "env.json").read_text())


def _result(values):
    return values["RESULT"][-1]


# --- member program (login node) --------------------------------------------------------------------------------


def test_create_writes_env_json_and_submits_the_builder_as_the_member(tree):
    code, values, _ = _create(tree)
    assert code == 0 and _result(values) == "created"
    d = tree["team"] / "cuda" / "12.4"
    data = _env_json(d)
    # the owner is the calling user, whatever the token said; free text is stored as data, never run
    assert data["owner"] == ME and data["status"] == "queued" and data["job_id"] == "4242"
    assert data["description"] == "CUDA $(reboot); `id` | rm -rf /"
    argv = tree["sbatch"].calls[0]
    assert argv[:5] == ["sbatch", "--parsable", "--account=teama", "--partition=build", "--qos=envbuild"]
    assert argv[-2:] == [str(tree["shared"] / "tools" / "hpc-env-build"), str(d)]
    assert f"--output={d}/build.log" in argv
    assert values["JOB"] == ["4242"] and values["TOTAL"] == ["0"] and values["DIR"] == [str(d)]
    # readable by everyone who may enter the name directory: the name directory alone decides the visibility
    assert _mode(d) == "0o755" and _mode(d / "env.json") == "0o644"


def test_existing_version_is_never_overwritten(tree):
    _create(tree)
    code, values, _ = _create(tree)
    assert _result(values) == "exists"
    assert len(tree["sbatch"].calls) == 1


def test_member_cannot_create_a_name_that_root_did_not_prepare(tree):
    _, values, _ = _create(tree, prepare=False)
    assert _result(values) == "not_found"
    assert list(tree["team"].iterdir()) == [] and tree["sbatch"].calls == []


def test_codebuild_create_returns_the_ids_and_stores_the_spec(tree):
    code, values, _ = _create(tree, name="app", builder="codebuild", spec_name="Dockerfile",
                              spec=_token("FROM ubuntu:22.04\nRUN echo hi\n"),
                              meta=_meta(source="dockerfile", image=None, ref=None, builder="codebuild"))
    assert _result(values) == "created"
    uid, gid = values["IDS"][0].split()
    assert int(uid) == os.getuid() and int(gid) == os.stat(tree["team"]).st_gid
    d = tree["team"] / "app" / "12.4"
    assert (d / "spec" / "Dockerfile").read_text() == "FROM ubuntu:22.04\nRUN echo hi\n"
    assert _mode(d / "spec") == "0o755" and _mode(d / "spec" / "Dockerfile") == "0o644"
    assert tree["sbatch"].calls == []


def test_cap_reached_blocks_new_builds(tree):
    _create(tree)
    d = tree["team"] / "cuda" / "12.4"
    data = _env_json(d)
    data.update(status="succeeded", size_bytes=900)
    (d / "env.json").write_text(json.dumps(data))
    _, values, _ = _create(tree, version="12.5", cap=1000)
    assert values["TOTAL"] == ["900"] and _result(values) == "created"
    _, values, _ = _create(tree, version="12.6", cap=900)
    assert _result(values) == "cap"
    assert not (tree["team"] / "cuda" / "12.6").exists()


def test_sbatch_failure_marks_the_version_failed(tree):
    tree["sbatch"].result = subprocess.CompletedProcess([], 1, "", "sbatch: error: Invalid qos specification\n")
    code, values, err = _create(tree)
    assert code == 1 and "Invalid qos" in err
    data = _env_json(tree["team"] / "cuda" / "12.4")
    assert data["status"] == "failed" and data["failure"].startswith("Could not submit the build job")


@pytest.mark.parametrize("argv", [
    ("teama", "../x", "1"),           # name with a path escape
    ("teama", "cuda", "../../x"),     # version with a path escape
    ("teama", "cuda;id", "1"),
    ("team a", "cuda", "1"),
    ("../teamb", "cuda", "1"),
])
def test_invalid_names_are_rejected(tree, argv):
    team, name, version = argv
    _, values, _ = tree["member"]("env-create", tree["envs"], tree["shared"], team, name, version, _meta(), "-", "-",
                                  0, "slurm")
    assert _result(values) == "invalid"
    assert list(tree["team"].iterdir()) == []


def test_symlinked_name_directory_is_denied(tree):
    (tree["envs"] / "teamb" / "cuda").mkdir()
    (tree["team"] / "cuda").symlink_to(tree["envs"] / "teamb" / "cuda")
    _, values, _ = _create(tree, prepare=False)
    assert _result(values) == "denied"
    assert list((tree["envs"] / "teamb" / "cuda").iterdir()) == []


def _version(tree, team, name, version, visibility, status="succeeded", link=True, **extra):
    """A version of another team as the layout migration or that team's members leave it."""
    ndir = tree["envs"] / team / name
    ndir.mkdir(exist_ok=True)
    os.chmod(ndir, 0o3775 if visibility == "all" else 0o3770)
    (ndir / version).mkdir()
    (ndir / version / "env.json").write_text(json.dumps({"kind": "conda", "status": status, "size_bytes": 5,
                                                         **extra}))
    if link:
        (tree["envs"] / "_public" / f"{team}__{name}").symlink_to(f"../{team}/{name}")
    return ndir


def test_list_returns_own_team_and_other_teams_public_envs(tree):
    _create(tree)
    _version(tree, "teamb", "torch", "2.3", "all", secret="x" * 10)
    _version(tree, "teamb", "private", "1", "team")             # stale index link, but 仅本团队: never listed
    closed = _version(tree, "teamb", "closed", "1", "all")
    os.chmod(closed, 0)                                          # what the kernel does when it is not readable
    (tree["envs"] / "_public" / "teama__cuda").symlink_to("../teama/cuda")  # own team: listed once
    (tree["envs"] / "_public" / "junk").symlink_to("/etc")
    (tree["envs"] / "_public" / "teamb__Bad").symlink_to("/etc")
    (tree["team"] / "evil").symlink_to(tree["envs"] / "teamb" / "torch")
    (tree["team"] / "cuda" / "link").symlink_to(tree["mount"])
    _, values, _ = tree["member"]("env-list", tree["envs"], "teama")
    listing = envs.parse_list(values)
    assert [(e["team"], e["name"], e["version"], e["status"], e["visibility"]) for e in listing["envs"]] == [
        ("teama", "cuda", "12.4", "queued", "team"), ("teamb", "torch", "2.3", "succeeded", "all")]
    own, other = listing["envs"]
    assert own["dir_owner"] == ME and own["path"] == str(tree["team"] / "cuda" / "12.4")
    assert other["path"] == str(tree["envs"] / "teamb" / "torch" / "2.3") and other["size_bytes"] == 5
    assert "secret" not in other and "published" not in other


def test_list_without_the_index_or_the_team_directory(tree):
    os.rmdir(tree["envs"] / "_public")
    _, values, _ = tree["member"]("env-list", tree["envs"], "teamc")
    assert envs.parse_list(values) == {"envs": [], "truncated": False}


def test_retry_only_failed_versions_of_the_owner(tree):
    _create(tree)
    d = tree["team"] / "cuda" / "12.4"
    _, values, _ = tree["member"]("env-retry", tree["envs"], tree["shared"], "teama", "cuda", "12.4", 0)
    assert _result(values) == "state"
    data = _env_json(d)
    data.update(status="failed", failure="boom")
    (d / "env.json").write_text(json.dumps(data))
    _, values, _ = tree["member"]("env-retry", tree["envs"], tree["shared"], "teama", "cuda", "12.4", 0)
    assert _result(values) == "retried" and values["BUILDER"] == ["slurm"] and values["DIR"] == [str(d)]
    data = _env_json(d)
    assert data["status"] == "queued" and data["failure"] is None and data["attempt"] == 2
    assert len(tree["sbatch"].calls) == 2


def test_codebuild_retry_returns_the_stored_dockerfile(tree):
    _create(tree, name="app", builder="codebuild", spec_name="Dockerfile", spec=_token("FROM x\n"),
            meta=_meta(source="dockerfile", image=None, builder="codebuild"))
    d = tree["team"] / "app" / "12.4"
    data = _env_json(d)
    data["status"] = "failed"
    (d / "env.json").write_text(json.dumps(data))
    _, values, _ = tree["member"]("env-retry", tree["envs"], tree["shared"], "teama", "app", "12.4", 0)
    assert values["BUILDER"] == ["codebuild"] and values["SOURCE"] == ["dockerfile"]
    assert gzip.decompress(base64.b64decode(values["SPEC"][0])) == b"FROM x\n"


def test_mark_records_build_id_and_failure(tree):
    _create(tree, builder="codebuild")
    d = tree["team"] / "cuda" / "12.4"
    tree["member"]("env-mark", tree["envs"], "teama", "cuda", "12.4", "hpc-poc-env-build:abc-123", "-")
    assert _env_json(d)["build_id"] == "hpc-poc-env-build:abc-123"
    _, values, _ = tree["member"]("env-mark", tree["envs"], "teama", "cuda", "12.4", "-", "x; reboot $(id)")
    assert _result(values) == "invalid"
    tree["member"]("env-mark", tree["envs"], "teama", "cuda", "12.4", "-",
                   "Could not start the CodeBuild build (Throttling)")
    data = _env_json(d)
    assert data["status"] == "failed" and data["failure"].endswith("(Throttling)")


# --- root program (head node) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("visibility,mode,link", [("team", "0o1770", False), ("all", "0o1775", True)])
def test_prepare_creates_the_name_directory_with_the_visibility(tree, visibility, mode, link):
    _, values, _ = _prepare(tree, visibility=visibility)
    assert _result(values) == "prepared" and values["VISIBILITY"] == [visibility]
    assert _mode(tree["team"] / "cuda") == mode
    index = tree["envs"] / "_public" / "teama__cuda"
    assert index.is_symlink() == link
    if link:
        assert os.readlink(index) == "../teama/cuda"  # relative, the same form as the layout migration
        assert index.resolve() == (tree["team"] / "cuda").resolve()


def test_prepare_keeps_the_visibility_of_an_existing_name(tree):
    _prepare(tree, visibility="all")
    _, values, _ = _prepare(tree, visibility="team")
    assert _result(values) == "prepared" and values["VISIBILITY"] == ["all"]
    assert _mode(tree["team"] / "cuda") == "0o1775"
    assert (tree["envs"] / "_public" / "teama__cuda").is_symlink()


def test_prepare_creates_a_missing_team_directory_with_the_team_group(tree):
    team_dir = tree["mount"] / "teamc"
    team_dir.mkdir()
    _, values, _ = _prepare(tree, team="teamc")
    assert _result(values) == "prepared"
    assert _mode(tree["envs"] / "teamc") == "0o751"
    gid = os.stat(team_dir).st_gid
    assert any(call.args[1:] == (-1, gid) for call in tree["fchown"].call_args_list)
    _, values, _ = tree["root"]("prepare", tree["envs"], "-", "teamd", "cuda", "team")
    assert _result(values) == "not_found"


@pytest.mark.parametrize("argv", [
    ("teama", "cuda", "public"),
    ("teama", "../x", "team"),
    ("../teamb", "cuda", "team"),
])
def test_prepare_rejects_invalid_values(tree, argv):
    team, name, visibility = argv
    _, values, _ = tree["root"]("prepare", tree["envs"], tree["mount"] / "teama", team, name, visibility)
    assert _result(values) == "invalid"
    assert list(tree["team"].iterdir()) == []


def test_root_operations_require_a_root_owned_envs_root(tree):
    os.chmod(tree["envs"], 0o777)
    _, values, _ = _prepare(tree)
    assert _result(values) == "denied"
    os.chmod(tree["envs"], 0o755)
    (tree["team"] / "evil").symlink_to(tree["envs"] / "teamb")
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "evil", "all", "-")
    assert _result(values) == "denied"


def test_visibility_change_checks_the_creator_and_maintains_the_index(tree):
    _create(tree)
    index = tree["envs"] / "_public" / "teama__cuda"
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "cuda", "all", "nobody-such-user")
    assert _result(values) == "not_owner" and _mode(tree["team"] / "cuda") == "0o1770"
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "cuda", "all", ME)  # creator of a version
    assert _result(values) == "changed" and values["OLD"] == ["team"] and values["VISIBILITY"] == ["all"]
    assert _mode(tree["team"] / "cuda") == "0o1775" and index.is_symlink()
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "cuda", "team", "-")  # lead or admin
    assert _result(values) == "changed" and values["OLD"] == ["all"]
    assert _mode(tree["team"] / "cuda") == "0o1770" and not index.exists() and not index.is_symlink()
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "nothere", "all", "-")
    assert _result(values) == "not_found"


def test_visibility_of_a_name_without_own_versions_is_not_owner(tree):
    _prepare(tree)  # a name with no version yet (or only versions of others)
    _, values, _ = tree["root"]("visibility", tree["envs"], "teama", "cuda", "all", ME)
    assert _result(values) == "not_owner"


def _succeeded(tree, name="cuda", version="12.4", **extra):
    _create(tree, name=name, version=version)
    d = tree["team"] / name / version
    data = _env_json(d)
    data.update(status="succeeded", size_bytes=4, **extra)
    (d / "env.json").write_text(json.dumps(data))
    (d / "image.sqsh").write_bytes(b"sqsh")
    return d


def test_delete_checks_the_owner_and_moves_into_the_trash(tree):
    d = _succeeded(tree)
    _succeeded(tree, version="12.5")
    _, values, _ = tree["root"]("delete", tree["envs"], "teama", "cuda", "12.4", "nobody")
    assert _result(values) == "not_owner" and d.exists()
    _, values, _ = tree["root"]("delete", tree["envs"], "teama", "cuda", "12.4", ME)
    assert _result(values) == "deleted" and not d.exists()
    assert (tree["team"] / "cuda" / "12.5").exists()  # other versions stay
    rm = [c for c in tree["sbatch"].calls if c[0] == "rm"]
    assert rm and rm[0][-1].startswith(str(tree["mount"] / ".pcui-trash") + "/")
    assert oct(os.stat(tree["mount"] / ".pcui-trash").st_mode & 0o777) == "0o700"


def test_deleting_the_last_version_removes_the_name_and_its_index_link(tree):
    _succeeded(tree)
    tree["root"]("visibility", tree["envs"], "teama", "cuda", "all", "-")
    index = tree["envs"] / "_public" / "teama__cuda"
    assert index.is_symlink()
    _, values, _ = tree["root"]("delete", tree["envs"], "teama", "cuda", "12.4", "-")
    assert _result(values) == "deleted"
    assert not (tree["team"] / "cuda").exists() and not index.is_symlink()


def test_delete_cancels_a_running_build(tree):
    _create(tree)
    d = tree["team"] / "cuda" / "12.4"
    data = _env_json(d)
    data.update(status="building", build_id="hpc-poc-env-build:1")
    (d / "env.json").write_text(json.dumps(data))
    _, values, _ = tree["root"]("delete", tree["envs"], "teama", "cuda", "12.4", "-")
    assert _result(values) == "deleted"
    # only a job of the directory owner is cancelled (env.json is editable by its owner)
    assert ["scancel", f"--user={ME}", "--", "4242"] in tree["sbatch"].calls
    assert values["BUILD"] == ["hpc-poc-env-build:1"]


@pytest.mark.parametrize("swap", ["name", "version"])
def test_delete_never_follows_a_swapped_link(tree, swap):
    victim = tree["envs"] / "teamb" / "cuda" / "12.4"
    victim.mkdir(parents=True)
    (victim / "env.json").write_text("{}")
    if swap == "name":
        (tree["team"] / "cuda").symlink_to(tree["envs"] / "teamb" / "cuda")
    else:
        (tree["team"] / "cuda").mkdir()
        (tree["team"] / "cuda" / "12.4").symlink_to(victim)
    _, values, _ = tree["root"]("delete", tree["envs"], "teama", "cuda", "12.4", "-")
    assert _result(values) == "denied"
    assert victim.exists()


def test_root_list_for_leads_shows_only_public_names_of_other_teams(tree):
    _create(tree)
    _version(tree, "teamb", "torch", "2.3", "all")
    _version(tree, "teamb", "private", "1", "team")  # root could read it: the mode decides, not the index
    _, values, _ = tree["root"]("list", tree["envs"], "teama")
    assert [(e["team"], e["name"]) for e in envs.parse_list(values)["envs"]] == [("teama", "cuda"),
                                                                                  ("teamb", "torch")]


def test_unknown_operation_and_wrong_arity_are_invalid(tree):
    assert _result(tree["root"]("chown", "/x")[1]) == "invalid"
    assert _result(tree["root"]("publish", tree["envs"], tree["shared"], "teama", "cuda", "12.4", "lead")[1]) \
        == "invalid"
    assert _result(tree["root"]("finalize", tree["shared"], "teama", "cuda", "12.4")[1]) == "invalid"
    assert _result(tree["member"]("env-list", tree["envs"])[1]) == "invalid"
    assert _result(tree["member"]("env-publish-build", tree["shared"], "teama", "cuda", "1")[1]) == "invalid"
    assert _result(tree["member"]("delete", tree["envs"], "teama", "a", "1", "-")[1]) == "invalid"
    assert _result(tree["member"]("prepare", tree["envs"], tree["mount"] / "teama", "teama", "a", "all")[1]) \
        == "invalid"
