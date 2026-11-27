"""In-memory fakes of the head node (AD, EFS team directories, Slurm) and the AWS APIs used by the team lifecycle."""
import shlex
from types import SimpleNamespace

from botocore.exceptions import ClientError

from api.teams.directory import AD_SCRIPT, ENV_OWNER_SCRIPT, TEAM_DIR_PROBE_SCRIPT, TEAM_DIR_SCRIPT
from api.teams.slurm import GEN_TYPE_QUOTA_SCRIPT

from . import slurm_outputs as out

HEAD_NODE = "i-0123456789abcdef0"
TEAM_DIR, ENV_DIR, ENV_TEAM_DIR = ("drwxrws---", "drwxrws--T", "drwxr-s--x")


def client_error(code, message="error", op="Op"):
    return ClientError({"Error": {"Code": code, "Message": message}}, op)


class FakeCluster:
    """Simulates the head node behind ssm_command: Simple AD (adcli/ldapsearch/ldapmodify), EFS and sacctmgr."""

    def __init__(self):
        self.commands = []
        self.ad_groups = {"teamA", "teamB"}
        self.ad_users = {"alice": None, "bob": None}  # user -> mail (None = no mail attribute)
        self.ad_members = {"teamA": {"alice"}, "teamB": {"bob"}}
        self.ad_disabled = set()
        self.dirs = {"/efs/teamA": (TEAM_DIR, "teamA"), "/efs/teamA/envs": (ENV_DIR, "teamA"),
                     "/efs/envs/teama": (ENV_TEAM_DIR, "teamA"),
                     "/efs/teamB": (TEAM_DIR, "teamB"), "/efs/teamB/envs": (ENV_DIR, "teamB"),
                     "/efs/envs/teamb": (ENV_TEAM_DIR, "teamB")}
        self.accounts = {"teama": 60, "teamb": 40}
        self.qos = {"teama-std": "node=0", "teamb-std": "node=0", "teama-g6e-xlarge": "node=2",
                    "teamb-g6e-12xlarge": "node=1"}
        self.qos_lists = {}  # account -> QOS list of the account association
        self.users = {("teama", "alice"), ("teamb", "bob")}
        self.user_qos = {}  # (account, user) -> QOS list
        self.max_submit = {}
        self.jobs = {}  # (account, user) -> number of queued/running jobs
        self.cancelled = []
        self.env_owner_runs = []
        self.gen_type_quota_runs = 0
        self.adcli_installed = True
        self.fail_slurm_writes = None

    # --- dispatch -----------------------------------------------------------------------------------------------

    def __call__(self, region, instance_id, user, command):
        assert (instance_id, user) == (HEAD_NODE, "root")
        self.commands.append(command)
        lines, failed = [], None
        segment, segments, separators = [], [], []
        for token in shlex.split(command):
            if token in (";", "&&"):
                segments.append(segment)
                separators.append(token)
                segment = []
            else:
                segment.append(token)
        segments.append(segment)
        for index, argv in enumerate(segments):
            if index and separators[index - 1] == "&&" and failed:
                break
            try:
                lines.extend(self._run(argv))
                failed = None
            except Exception as err:  # mirrors ssm_command raising on a failed command
                failed = err
        if failed:
            raise failed
        return "\n".join(lines)

    def _run(self, argv):
        if argv[:1] == ["echo"]:
            return [argv[1]]
        if argv[:3] == ["bash", "-c", AD_SCRIPT]:
            return self._ad(argv[4:])
        if argv[:3] == ["bash", "-c", TEAM_DIR_SCRIPT]:
            path, group, team = argv[4], argv[5], argv[6]
            if group not in self.ad_groups:
                raise Exception(f"AD group {group} does not resolve")
            self.dirs[path] = (TEAM_DIR, group)
            self.dirs[path + "/envs"] = (ENV_DIR, group)
            self.dirs[f"/efs/envs/{team}"] = (ENV_TEAM_DIR, group)
            return [f"@@TEAMDIR {TEAM_DIR} {group}", f"@@ENVDIR {ENV_DIR} {group}",
                    f"@@ENVTEAMDIR {ENV_TEAM_DIR} {group}"]
        if argv[:3] == ["bash", "-c", TEAM_DIR_PROBE_SCRIPT]:
            result = []
            for marker, path in (("@@TEAMDIR", argv[4]), ("@@ENVDIR", argv[4] + "/envs"),
                                 ("@@ENVTEAMDIR", f"/efs/envs/{argv[5]}")):
                state = self.dirs.get(path)
                result.append(f"{marker} {state[0]} {state[1]}" if state else f"{marker} missing")
            return result
        if argv[:3] == ["bash", "-c", ENV_OWNER_SCRIPT]:
            self.env_owner_runs.append(tuple(argv[4:]))
            return ["@@ENVOWNER 2"]
        if argv[:3] == ["bash", "-c", GEN_TYPE_QUOTA_SCRIPT]:
            assert argv[4] == "/opt/slurm/etc/gen-type-quota.sh"
            self.gen_type_quota_runs += 1
            return []
        if argv[0].endswith("sacctmgr"):
            return self._sacctmgr(argv[1:])
        if argv[0].endswith("scancel"):
            assert argv[1] == "-u" and argv[3] == "-A"
            self.cancelled.append((argv[4], argv[2]))
            self.jobs.pop((argv[4], argv[2]), None)
            return []
        if argv[0].endswith("squeue"):
            if "-u" in argv:
                key = (argv[argv.index("-A") + 1], argv[argv.index("-u") + 1])
                return [str(100 + i) for i in range(self.jobs.get(key, 0))]
            return []
        raise AssertionError(f"unexpected command {argv[:3]}")

    # --- AD -----------------------------------------------------------------------------------------------------

    def _ad(self, args):
        region, secret, domain, dc, admin, group, *ops = args
        assert secret.startswith("arn:aws:secretsmanager:")
        if not self.adcli_installed:
            return ["@@AD error 3"]
        result = []
        for op in ops:
            kind, _, arg = op.partition(":")
            if kind == "lookup":
                if arg not in self.ad_users:
                    result.append(f"@@AD user {arg} absent")
                elif self.ad_users[arg]:
                    result.append(f"@@AD user {arg} mail={self.ad_users[arg]}")
                else:
                    result.append(f"@@AD user {arg} nomail")
            elif kind == "probe-group":
                result.append(f"@@AD group {group} {'exists' if group in self.ad_groups else 'absent'}")
            elif kind == "probe-member":
                present = arg in self.ad_members.get(group, set())
                result.append(f"@@AD member {arg} {'present' if present else 'absent'}")
            elif kind == "create-group":
                result.append(f"@@AD group {group} {'exists' if group in self.ad_groups else 'created'}")
                self.ad_groups.add(group)
            elif kind == "create-user":
                user, _, mail = arg.partition(":")
                result.append(f"@@AD user {user} {'exists' if user in self.ad_users else 'created'}")
                self.ad_users.setdefault(user, mail)
            elif kind == "add":
                members = self.ad_members.setdefault(group, set())
                result.append(f"@@AD member {arg} {'present' if arg in members else 'added'}")
                members.add(arg)
            elif kind == "remove":
                members = self.ad_members.setdefault(group, set())
                result.append(f"@@AD member {arg} {'removed' if arg in members else 'absent'}")
                members.discard(arg)
            elif kind in ("disable", "enable"):
                if arg not in self.ad_users:
                    result.append(f"@@AD user {arg} absent")
                else:
                    (self.ad_disabled.add if kind == "disable" else self.ad_disabled.discard)(arg)
                    result.append(f"@@AD user {arg} {kind}d")
        return result

    # --- Slurm --------------------------------------------------------------------------------------------------

    def _assoc_qos(self, account, user=None):
        if user is not None and (account, user) in self.user_qos:
            return self.user_qos[(account, user)]
        return self.qos_lists.get(account, f"{account}-std,borrow")

    def _sacctmgr(self, args):
        if args[:4] == ["-n", "-P", "show", "assoc"]:
            lines = ["root||1|normal|", "root|root|1|normal|"]
            for account, fairshare in sorted(self.accounts.items()):
                lines.append(f"{account}||{fairshare}|{self._assoc_qos(account)}|{account}-std")
                for acc, user in sorted(self.users):
                    if acc == account:
                        lines.append(f"{account}|{user}|1|{self._assoc_qos(account, user)}|{account}-std")
            return lines
        if args[:4] == ["-n", "-P", "show", "qos"]:
            return ["normal|0||", *[f"{n}|100|{t}|" for n, t in sorted(self.qos.items())], "borrow|10||04:00:00",
                    "envbuild|50|node=2|03:00:00"]
        assert args[0] == "-i", args
        if self.fail_slurm_writes:
            raise Exception(self.fail_slurm_writes)
        verb = args[1:3]
        if verb == ["add", "account"]:
            self.accounts[args[3]] = int(args[-1].split("=")[1])
        elif verb == ["add", "qos"]:
            self.qos[args[3]] = args[-1].split("=", 1)[1]
        elif verb == ["add", "user"]:
            account = args[4].split("=")[1]
            self.users.add((account, args[3]))
            self.user_qos[(account, args[3])] = args[5].split("=", 1)[1]
        elif verb == ["modify", "account"]:
            qos = next((a.split("=", 1)[1] for a in args if a.startswith("QOS=")), None)
            if qos:
                self.qos_lists[args[4].split("=")[1]] = qos
        elif verb == ["modify", "user"]:
            where = dict(a.split("=", 1) for a in args[4:args.index("set")])
            sets = dict(a.split("=", 1) for a in args[args.index("set") + 1:])
            for account, user in list(self.users):
                if account == where.get("account") and where.get("name", user) == user:
                    if "QOS" in sets:
                        self.user_qos[(account, user)] = sets["QOS"]
                    if "MaxSubmitJobs" in sets:
                        self.max_submit[(account, user)] = int(sets["MaxSubmitJobs"])
        elif verb == ["modify", "qos"]:
            name = args[4].split("=")[1]
            self.qos[name] = args[-1].split("=", 1)[1]
        elif verb == ["delete", "user"]:
            name, account = args[4].split("=")[1], args[5].split("=")[1]
            self.users.discard((account, name))
        elif verb == ["delete", "account"]:
            self.accounts.pop(args[4].split("=")[1])
        elif verb == ["delete", "qos"]:
            self.qos.pop(args[4].split("=")[1])
        else:
            raise AssertionError(f"unexpected sacctmgr {args}")
        return []


class _Recorder:
    def __init__(self):
        self.calls = []
        self.fail = {}

    def _record(self, name, kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise self.fail[name]


class FakeECR(_Recorder):
    def __init__(self):
        super().__init__()
        self.repos = {}  # name -> {tags, scan, encryption, lifecycle}

    def describe_repositories(self, **kwargs):
        self._record("describe_repositories", kwargs)
        name = kwargs["repositoryNames"][0]
        if name not in self.repos:
            raise client_error("RepositoryNotFoundException")
        return {"repositories": [{"repositoryName": name,
                                  "repositoryArn": f"arn:aws:ecr:us-west-2:123456789012:repository/{name}"}]}

    def create_repository(self, **kwargs):
        self._record("create_repository", kwargs)
        if kwargs["repositoryName"] in self.repos:
            raise client_error("RepositoryAlreadyExistsException")
        self.repos[kwargs["repositoryName"]] = {"tags": kwargs["tags"], "scan": kwargs["imageScanningConfiguration"],
                                                "encryption": kwargs["encryptionConfiguration"]}

    def tag_resource(self, **kwargs):
        self._record("tag_resource", kwargs)
        name = kwargs["resourceArn"].rsplit("/", 1)[1]
        self.repos[name]["tags"] = kwargs["tags"]

    def put_lifecycle_policy(self, **kwargs):
        self._record("put_lifecycle_policy", kwargs)
        self.repos[kwargs["repositoryName"]]["lifecycle"] = kwargs["lifecyclePolicyText"]


class FakeDS(_Recorder):
    def __init__(self):
        super().__init__()
        self.passwords = {}

    def reset_user_password(self, **kwargs):
        self._record("reset_user_password", {k: v for k, v in kwargs.items() if k != "NewPassword"})
        assert kwargs["DirectoryId"] == "d-0123456789"
        self.passwords[kwargs["UserName"]] = kwargs["NewPassword"]


class FakeSecrets(_Recorder):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.tags = {}

    def create_secret(self, **kwargs):
        self._record("create_secret", {k: v for k, v in kwargs.items() if k != "SecretString"})
        if kwargs["Name"] in self.values:
            raise client_error("ResourceExistsException")
        self.values[kwargs["Name"]] = kwargs["SecretString"]
        self.tags[kwargs["Name"]] = kwargs["Tags"]

    def put_secret_value(self, **kwargs):
        self._record("put_secret_value", {k: v for k, v in kwargs.items() if k != "SecretString"})
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]


class FakeCognito(_Recorder):
    def __init__(self):
        super().__init__()
        self.groups = {"admin": set(), "team-teama": set(), "team-teamb": set()}
        self.users = {}  # email -> username
        self.disabled = set()
        self.signed_out = []
        self.temp_passwords = {}
        self.welcome_emails = []

    def get_group(self, **kwargs):
        self._record("get_group", kwargs)
        if kwargs["GroupName"] not in self.groups:
            raise client_error("ResourceNotFoundException")
        return {"Group": {"GroupName": kwargs["GroupName"]}}

    def create_group(self, **kwargs):
        self._record("create_group", kwargs)
        self.groups[kwargs["GroupName"]] = set()

    def delete_group(self, **kwargs):
        self._record("delete_group", kwargs)
        if kwargs["GroupName"] not in self.groups:
            raise client_error("ResourceNotFoundException")
        del self.groups[kwargs["GroupName"]]

    def admin_get_user(self, **kwargs):
        self._record("admin_get_user", kwargs)
        if kwargs["Username"] not in self.users:
            raise client_error("UserNotFoundException")
        return {"Username": self.users[kwargs["Username"]]}

    def admin_create_user(self, **kwargs):
        self._record("admin_create_user", {k: v for k, v in kwargs.items() if k != "TemporaryPassword"})
        if "TemporaryPassword" in kwargs:  # welcome e-mail off: our password, nothing sent
            assert kwargs["MessageAction"] == "SUPPRESS"
            self.temp_passwords[kwargs["Username"]] = kwargs["TemporaryPassword"]
        else:  # welcome e-mail on: Cognito generates the password and e-mails it
            assert "MessageAction" not in kwargs and kwargs["DesiredDeliveryMediums"] == ["EMAIL"]
            self.welcome_emails.append(kwargs["Username"])
        self.users[kwargs["Username"]] = f"uuid-{kwargs['Username']}"

    def admin_add_user_to_group(self, **kwargs):
        self._record("admin_add_user_to_group", kwargs)
        assert kwargs["Username"] in self.users
        self.groups[kwargs["GroupName"]].add(kwargs["Username"])

    def admin_remove_user_from_group(self, **kwargs):
        self._record("admin_remove_user_from_group", kwargs)
        email = next(e for e, u in self.users.items() if u == kwargs["Username"])
        self.groups[kwargs["GroupName"]].discard(email)

    def admin_disable_user(self, **kwargs):
        self._record("admin_disable_user", kwargs)
        self.disabled.add(kwargs["Username"])

    def admin_enable_user(self, **kwargs):
        self._record("admin_enable_user", kwargs)
        self.disabled.discard(kwargs["Username"])

    def admin_user_global_sign_out(self, **kwargs):
        self._record("admin_user_global_sign_out", kwargs)
        self.signed_out.append(kwargs["Username"])

    def list_users_in_group(self, **kwargs):
        self._record("list_users_in_group", kwargs)
        if kwargs["GroupName"] not in self.groups:
            raise client_error("ResourceNotFoundException")
        return {"Users": [{"Username": self.users.get(e, e), "Attributes": [{"Name": "email", "Value": e}]}
                          for e in sorted(self.groups[kwargs["GroupName"]])]}


class FakeSNS(_Recorder):
    def __init__(self):
        super().__init__()
        self.subscriptions = {}  # arn -> {endpoint, filter, pending}
        self.published = []

    def subscribe(self, **kwargs):
        self._record("subscribe", kwargs)
        arn = f"{kwargs['TopicArn']}:sub-{len(self.subscriptions) + 1}"
        self.subscriptions[arn] = {"endpoint": kwargs["Endpoint"], "protocol": kwargs["Protocol"],
                                   "filter": kwargs["Attributes"]["FilterPolicy"], "pending": True}
        return {"SubscriptionArn": arn}

    def unsubscribe(self, **kwargs):
        self._record("unsubscribe", kwargs)
        self.subscriptions.pop(kwargs["SubscriptionArn"], None)

    def get_subscription_attributes(self, **kwargs):
        self._record("get_subscription_attributes", kwargs)
        sub = self.subscriptions.get(kwargs["SubscriptionArn"])
        if sub is None:
            raise client_error("NotFound")
        return {"Attributes": {"PendingConfirmation": "true" if sub["pending"] else "false"}}

    def publish(self, **kwargs):
        self._record("publish", kwargs)
        self.published.append(kwargs)


def fake_clients():
    return SimpleNamespace(ecr=FakeECR(), ds=FakeDS(), secrets=FakeSecrets(), cognito=FakeCognito())


QUOTA_OUTPUTS = {"QUEUE": out.QUEUE, "SINFO": out.SINFO, "TEAMJOBS": out.TEAM_JOBS}
