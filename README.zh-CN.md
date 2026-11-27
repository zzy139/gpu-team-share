# ParallelCluster UI 定制版：多团队 GPU 配额与费用

本仓库基于开源的 [AWS ParallelCluster UI](https://github.com/aws/aws-parallelcluster-ui)（Apache-2.0，基线版本 `2026.10.0`，对应 ParallelCluster 3.16.2），在不改动上游功能的前提下增加了三项功能：

1. **团队配额管理页**（仅管理员）
2. **团队用量与费用页**
3. **团队负责人视图**（只读，只能看到自己团队）

新代码放在独立模块中（后端 `api/teams/`，前端 `frontend/src/team-quota/`），以便日后合并上游新版本。

---

## 1. 功能概述

### 1.1 团队配额（菜单 “Team quota”，路径 `/pcui/teams`）

列出集群上所有 Slurm 团队账号（排除 `root`、`pcdefault`），每行显示：

| 列 | 来源 |
|---|---|
| Fairshare（占比） | `sacctmgr show assoc` 中账号行的 Fairshare |
| Billing limit | 团队 QOS `<team>-std` 的 `GrpTRES=billing=N` |
| GPU limits | 同一 QOS 中的 `gres/gpu[:类型]=N` |
| Members | 该账号下的用户 |
| Running now | `sacct -s RUNNING` 汇总的 billing 点数、GPU 数、作业数 |
| % of limit | 运行中 billing ÷ billing 上限 |

管理员可以：

- **Edit limits**：修改 fairshare、billing 上限、GPU 上限（留空表示取消该 GPU 上限）。
- **Create team**：创建账号 `<team>`、QOS `<team>-std`（Priority=100），并授予 `<team>-std,borrow` 两个 QOS，默认 QOS 为 `<team>-std`。
- **Manage members**：把 Linux 用户加入团队（自动授予 `<team>-std,borrow`），或从团队移除。
- **Delete**：删除没有成员的团队（账号和 `<team>-std` QOS）。

GPU 上限只针对 Slurm 记账中实际存在的 TRES 提供输入框（页面上的 `available_gpu_tres`）。hpc-poc 目前只记账 `gres/gpu`（不区分型号），因此只能设置总 GPU 上限，见第 8 节。

### 1.2 团队用量与费用（菜单 “Team usage & cost”，路径 `/pcui/team-usage`）

- 期间选择：本月、上月、最近 30 天、自定义（结束日包含在内）。
- 每个团队：GPU 小时、billing 小时、billing 占比、**Billed**（Cost Explorer 实际账单）的计算费用 / 共享费用 / 合计，以及 **Estimate**（估算）GPU 费用。
- 管理员额外看到 “Cluster totals and shared costs”：项目总计算费用、共享费用、总费用、未分摊金额、估算总额。
- 月度趋势图：最近 6 个月每个团队的账单费用或 GPU 小时（堆叠柱状图）。

### 1.3 团队负责人视图

属于 Cognito 组 `team-<team>`（例如 `team-teama`）且不属于 `admin` 组的用户登录后：

- 只看到 “Team quota” 和 “Team usage & cost” 两个菜单，首页跳转到 `/pcui/team-usage`。
- 只看到自己团队的配额、用量和费用；不显示集群总额和其他团队。
- 所有页面只读。

集群的队列、实例类型、MaxCount 等服务器配置仍使用 PCUI 自带的集群编辑向导（Clusters → 选中集群 → Actions → Edit cluster），本定制没有改动它。

---

## 2. 架构与权限模型

```
浏览器 ──HTTPS──> API Gateway ──> Lambda（Flask，本仓库镜像）
                                   │  /team-quota/*  （新蓝图 api/teams）
                                   ├─ ParallelCluster API：按集群名查询头节点实例 ID
                                   ├─ SSM SendCommand（AWS-RunShellScript）→ 头节点上以 root 运行 sacctmgr/sacct/sreport
                                   ├─ 成员接口：AssumeRole PcuiMemberExecRole → SSM SendCommand → 登录节点上 sudo -u <成员> 运行 squeue/sacct/sbatch/scancel
                                   ├─ 团队生命周期：头节点 adcli / mkdir；S3、FSx DRA、Secrets Manager、Directory Service、Cognito API
                                   ├─ Cost Explorer（us-east-1）→ 按标签取账单
                                   └─ Pricing API（us-east-1）+ EC2 DescribeInstanceTypes → 每 GPU 按需单价
```

### 2.1 身份与授权（全部在服务端强制执行）

- 登录仍使用 PCUI 自带的 Cognito 用户池。授权依据访问令牌中的 `cognito:groups`。
- 每个新接口都使用上游的 `@authenticated(...)` 装饰器：
  - 读接口（`GET /team-quota/clusters|teams|usage|trend`）：`admin` 组，或任意 `team-` 前缀组。
  - 写接口（`POST/PATCH/DELETE /team-quota/teams...`）：只允许 `admin`，并且依次经过 `@csrf_needed` 和 `@validated`。
- 团队范围由 `api/teams/scope.py` 决定：管理员看全部；其他用户只能看到 `team-<team>` 组对应的团队。显式请求别的团队（`team=teamb`）返回 **403**。列表与用量结果按范围过滤，非管理员的响应中不包含集群总额。
- 未登录请求返回 **401**；团队负责人调用管理员接口（包括上游 `/api`、`/manager/list_users` 等）返回 **403**。
- 为了让团队负责人和成员能加载界面，上游 `/manager/get_identity` 也接受 `team-` 和 `member-` 前缀组。它只返回调用者自己的令牌声明。
- 业务成员（Cognito 组 `member-<team>`）只能调用 `/team-quota/member/*`，见第 12 节。上游作业、日志和 Shell 相关接口（`/manager/queue_status`、`/manager/sacct`、`/manager/scontrol_job`、`/manager/cancel_job`、`/logs`、`/manager/get_dcv_session`、`/manager/ec2_action`）保持只允许 `admin`，`api/tests/teams/test_upstream_admin_only.py` 防止回退。

### 2.2 头节点命令安全

- 头节点实例 ID 永远由服务端根据 `cluster_name` 通过 ParallelCluster API 解析（缓存 300 秒），不接受客户端传入的实例 ID；集群必须是 Slurm 调度器。
- 所有输入都经过白名单校验（`api/teams/validation.py`）：
  - 团队名：`^[a-z][a-z0-9_-]{0,31}$`（生命周期新建的团队不允许 `_`：`^[a-z][a-z0-9-]{0,31}$`）；`root`、`pcdefault`、`normal`、`borrow` 为保留名，不可修改或删除。
  - 集群用户名（AD sAMAccountName = Linux = Slurm 用户）：`^[a-z][a-z0-9._-]{0,19}$`；`root`、`slurm` 不可添加或移除。
  - 邮箱：`^[a-z0-9._-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+$`，先转小写，总长 ≤ 128，域名必须在 `EmailDomains` 允许列表中。
  - Fairshare 1–10000；billing 上限 0–100000；GPU 上限 -1（取消）–10000；必须是整数（JSON 中的字符串、小数、布尔值都会被拒绝）。
  - GPU TRES 名：`^gres/gpu(:[a-z0-9_]{1,16})?$`，并且必须出现在头节点 `sacctmgr show tres` 中。
  - 日期 `YYYY-MM-DD`，开始早于结束，跨度不超过 366 天；趋势月数 1–12。
- 命令以参数列表构造，每个参数用 `shlex.quote` 转义后拼接，二进制使用绝对路径 `/opt/slurm/bin/...`。请求中的原始字符串从不直接拼进 shell。
- 写操作在同一次 SSM 调用中用 `&&` 串联，前一步失败即停止。Slurm 报错返回 400 并显示给管理员；对团队负责人隐藏错误细节。

### 2.3 审计日志

每次写操作（成功、失败或被拒绝）都会写一条 `team-quota-audit` 日志到 Lambda 的 CloudWatch 日志组，字段包括 `actor`（Cognito 用户名）、`actor_email`、`action`、`cluster`、`team`、`params`、`result`、`error`、`time`。查询示例：

```bash
aws logs filter-log-events --region us-west-2 \
  --log-group-name /aws/lambda/<ParallelClusterUIFun 函数名> \
  --filter-pattern team-quota-audit
```

### 2.4 IAM
官方 PCUI 模板**不修改**（模板超过 51,200 字节，修改后必须从 S3 部署，也会和上游冲突）。新增权限全部放在旁路堆栈 `infrastructure/team-lifecycle/pcui-team-lifecycle.yaml`（部署为 `pcui-custom-teams`，见 11.1）：

| 资源 | 作用 | 范围 |
|---|---|---|
| `PcuiSsmScopePolicy`（附加到 PCUI Lambda 角色） | 显式 Deny：Lambda 只能对 `parallelcluster:cluster-name ∈ ClusterNames` 且 `parallelcluster:node-type = HeadNode` 的实例执行 `ssm:SendCommand`；拒绝 managed-instance | 缺少标签视为不匹配，未打标签的实例一律拒绝；上游 `SsmPolicy` 以后放宽也不受影响 |
| `PcuiMemberExecRole` | 成员接口专用角色，Lambda 只在成员接口中 `sts:AssumeRole` | 信任策略只允许 PCUI Lambda 角色；只能对 `node-type = LoginNode` 且属于所列集群的实例 SendCommand，另有对非 LoginNode 的显式 Deny |
| `PcuiTeamLifecyclePolicy`（附加到 PCUI Lambda 角色） | 团队生命周期 | `ds:ResetUserPassword` 限定目录；Secrets Manager 限定 `<Project>/user-*`（创建时必须带 `Project` 标签，**没有** `GetSecretValue`）；S3 限定 `<Project>-*-<账号>-<区域>`；FSx 限定文件系统；IAM 只限该文件系统的 FSx S3 服务关联角色；Cognito 限定用户池；SSM 参数限定 `<TeamRecordPrefix>/config` 和 `/teams/*`（没有删除权限） |
| `HeadNodeAdAdminPolicy`（附加到集群头节点） | 头节点自行读取 AD 管理员密码供 adcli 使用 | 只有 `secretsmanager:GetSecretValue`，只限 `AdAdminSecretArn` |
| `<TeamRecordPrefix>/config`（SSM 参数） | 功能配置 | 不含任何密码 |

其余权限沿用官方模板：`ce:GetCostAndUsage`、`pricing:GetProducts`、`ec2:DescribeInstanceTypes`、CloudWatch Logs。头节点角色需要能把 SSM 输出写到 PCUI 的 SSM 日志组（ParallelCluster 默认附带的 `CloudWatchAgentServerPolicy` 已满足）。

**已知取舍**：PCUI Lambda 可以在头节点上以 root 执行命令，而头节点可以读取 AD 管理员密码，因此 PCUI 管理员角色被攻破即等同于 AD 管理员被攻破。请把 PCUI 管理员控制在少数人，并按第 13 节加固。

---

## 3. 构建镜像与部署

前提：AWS CLI v2、Docker（或兼容的容器运行时，需支持 `linux/amd64`）、Node.js 18+。Lambda 是 x86_64 架构，在 Apple Silicon 上构建时脚本会自动加 `--platform linux/amd64`。

### 3.1 创建私有 ECR 仓库（一次性）

```bash
export AWS_REGION=us-west-2
aws ecr create-repository --repository-name pcui-custom \
  --image-scanning-configuration scanOnPush=true
```

不要给仓库设置资源策略，保持私有。

### 3.2 构建并推送镜像

```bash
cd frontend && npm ci && cd ..
scripts/build_custom_image.sh --region us-west-2 --tag 2026.10.0-teams.1
# 输出：<账号>.dkr.ecr.us-west-2.amazonaws.com/pcui-custom:2026.10.0-teams.1
```

### 3.2b 没有本地 Docker 时：用 AWS CodeBuild 构建

`infrastructure/custom-build/pcui-image-build.yaml` 会创建一个小堆栈：私有 ECR 仓库（删除堆栈时保留）、源码 S3 桶（开启全部 Block Public Access、7 天自动过期、仅允许 HTTPS）、CodeBuild 项目（x86_64、特权模式运行 Docker）及最小权限的构建角色。

```bash
export AWS_REGION=us-west-2
aws cloudformation deploy --region $AWS_REGION --stack-name pcui-custom-build \
  --template-file infrastructure/custom-build/pcui-image-build.yaml --capabilities CAPABILITY_IAM
BUCKET=$(aws cloudformation describe-stacks --region $AWS_REGION --stack-name pcui-custom-build \
  --query "Stacks[0].Outputs[?OutputKey=='SourceBucketName'].OutputValue" --output text)
git archive --format=zip -o /tmp/pcui-src.zip HEAD          # 只打包已提交的文件
aws s3 cp --region $AWS_REGION /tmp/pcui-src.zip s3://$BUCKET/pcui-src.zip
aws codebuild start-build --region $AWS_REGION --project-name pcui-custom-image \
  --environment-variables-override name=IMAGE_TAG,value=2026.10.0-teams.1,type=PLAINTEXT
# 构建成功后删除源码包
aws s3 rm --region $AWS_REGION s3://$BUCKET/pcui-src.zip
```

构建日志在 CloudWatch 日志组 `/aws/codebuild/pcui-custom-image`。镜像 URI 为 `<账号>.dkr.ecr.<区域>.amazonaws.com/pcui-custom:<tag>`。

### 3.3 部署 PCUI 堆栈

使用官方 PCUI CloudFormation 模板，把 `PublicEcrImageUri` 指向上一步的镜像：

```bash
aws cloudformation create-stack --region us-west-2 --stack-name pcui-custom \
  --template-url https://parallelcluster-ui-release-artifacts-us-east-1.s3.us-east-1.amazonaws.com/parallelcluster-ui.yaml \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    ParameterKey=AdminUserEmail,ParameterValue=<管理员邮箱> \
    ParameterKey=Version,ParameterValue=3.16.2 \
    ParameterKey=PublicEcrImageUri,ParameterValue=<上一步输出的镜像 URI>
aws cloudformation wait stack-create-complete --region us-west-2 --stack-name pcui-custom
aws cloudformation describe-stacks --region us-west-2 --stack-name pcui-custom \
  --query 'Stacks[0].Outputs[?OutputKey==`ParallelClusterUIUrl`].OutputValue' --output text
```

建议不要给 PCUI 堆栈打集群的 `Project` 标签，避免 PCUI 自身费用被计入集群共享费用。

### 3.4 只更新代码（堆栈已存在）

构建新标签的镜像后，用 `update-stack` 更新 `PublicEcrImageUri`（其余参数 `UsePreviousValue=true`）。也可以用上游脚本 `scripts/build_and_update_lambda.sh` 直接更新 Lambda 镜像（该脚本没有 `--platform`，在 ARM 机器上需要自行加上）。

### 3.5 部署后检查

```bash
URL=<ParallelClusterUIUrl 去掉末尾 /pcui>
curl -s -o /dev/null -w '%{http_code}\n' "$URL/pcui/team-quota/teams?cluster_name=hpc-poc"   # 期望 401
```

返回 401 说明定制镜像已生效（上游镜像对未知路径会返回首页 200）。

---

## 4. 创建团队负责人

团队负责人是 PCUI Cognito 用户池中的用户，加入 `team-<team>` 组即可（`<team>` 为 Slurm 账号名，全小写）。

```bash
POOL_ID=<PCUI 用户池 ID>   # CloudFormation 堆栈资源中的 Cognito 用户池
aws cognito-idp create-group --user-pool-id "$POOL_ID" --group-name team-teama
aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username lead-a@example.com \
  --user-attributes Name=email,Value=lead-a@example.com Name=email_verified,Value=true
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" \
  --username lead-a@example.com --group-name team-teama
```

- 用户会收到 Cognito 发送的临时密码邮件，首次登录时修改。
- 一个人可以加入多个 `team-*` 组，从而查看多个团队。
- 不要把团队负责人加入 `admin` 组，否则他会获得全部管理权限。
- PCUI 自带的 Users 页面创建的用户默认进入 `admin` 组；团队负责人请用上面的命令创建。

---

## 5. 使用说明

### 5.1 管理员

1. 登录后，左侧菜单点击 **Team quota**，在 Cluster 下拉框中选择集群（默认第一个 Slurm 集群）。
2. 修改配额：选中团队 → **Edit limits** → 修改 Billing limit（例如 teamb 由 4 改为 6）→ Save。可以在头节点上确认：
   ```bash
   /opt/slurm/bin/sacctmgr -n -P show qos teamb-std format=Name,GrpTRES
   ```
3. 新建团队：**Create team** → 输入团队名、Fairshare、Billing limit → Create。然后 **Manage members** 添加成员。成员必须是集群上存在的 Linux 用户（例如来自 AD 的用户）。
4. 删除团队：先在 **Manage members** 中移除所有成员，再 **Delete**。有运行中作业的成员可能无法移除（Slurm 会报错）。
5. 菜单 **Team usage & cost** 查看各团队用量和费用，选择期间，下方查看月度趋势。

### 5.2 团队负责人

登录后直接进入 **Team usage & cost**，只显示自己团队；**Team quota** 页面只读。

### 5.3 接口（供脚本调用）

所有接口前缀为 `/pcui/team-quota`，查询参数 `cluster_name`（必填）和 `region`（可选，默认 PCUI 所在区域）。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/clusters` | admin、team-* | Slurm 集群列表 |
| GET | `/teams` | admin、team-* | 团队列表，可选 `team` |
| POST | `/teams` | admin | `{name, fairshare, billing_limit, gpu_limits?}` |
| PATCH | `/teams/<team>` | admin | `{fairshare?, billing_limit?, gpu_limits?}` |
| DELETE | `/teams/<team>` | admin | 删除成员关联、Slurm 账号和 QOS，以及 `team-<team>`、`member-<team>` 组；返回 `removed` 和 `retained` |
| POST | `/teams/<team>/members` | admin | 生命周期团队 `{email, link_existing?}`；旧团队 `{user}` |
| DELETE | `/teams/<team>/members/<user>` | admin | 同时移出 AD 组和 `member-<team>` 组，用户本身保留 |
| GET | `/lifecycle/teams` | admin | `{configured, records, email_domains}` |
| GET | `/lifecycle/teams/<team>` | admin | 各步骤状态（实时探测） |
| POST | `/lifecycle/teams` | admin | `{name, fairshare, billing_limit, gpu_limits?, members:[{email, link_existing?}], lead?}`，返回 202 |
| POST | `/lifecycle/teams/<team>/advance` | admin | `{}`，执行下一个未完成步骤 |
| GET | `/member/me`、`/member/options` | member-* | 当前成员、团队目录、集群、可选队列与上限 |
| GET | `/member/jobs`、`/member/history`、`/member/jobs/<id>` | member-* | 自己的作业 |
| GET | `/member/files`、`/member/dir` | member-* | 读文件（每次 ≤ 16 KiB，tail/offset 分页）、列目录 |
| POST | `/member/jobs`、`/member/jobs/<id>/cancel` | member-* | 提交、取消自己的作业 |
| GET | `/member/team` | member-* | 本团队配额、用量、本人用量 |
| GET | `/usage` | admin、team-* | `start`、`end`（不含）、可选 `team`、`project` |
| GET | `/trend` | admin、team-* | `months`（默认 6）、可选 `team`、`project` |

写接口需要 PCUI 的 CSRF 头（浏览器界面会自动处理）。

---

## 6. 费用口径

### 6.1 用量

- 来源：头节点 `sreport -t minutes cluster AccountUtilizationByUser -T billing,gres/gpu`，取账号汇总行，除以 60 得到小时。用分钟而不是小时，是因为 `-t hours` 会把几分钟的短作业取整为 0。
- billing 点数由分区的 `TRESBillingWeights` 决定：hpc-poc 上 g6 队列每 GPU 1 点，g6e 队列每 GPU 2 点。

### 6.2 实际账单（Billed）

- 来源：Cost Explorer，指标 `UnblendedCost`。
  - **计算费用**：标签 `Project=<项目>` 且 `parallelcluster:node-type=Compute`。
  - **总费用**：标签 `Project=<项目>`。
  - **共享费用** = 总费用 − 计算费用（头节点、登录节点、FSx、RDS、Simple AD、NAT 等）。
- `<项目>` 默认等于集群名（hpc-poc）。只有 admin 可以用 `project` 参数指定其他项目标签；团队负责人传入其他值会得到 403。
- 每个团队分摊的计算费用和共享费用 = 对应总额 × 该团队 billing 点数占比。期间内没有任何 billing 用量时，费用记为 “未分摊”。
- Cost Explorer 数据有约 24 小时延迟；新激活的成本分配标签也需要一段时间才生效。因此当天或刚跑完的作业可能显示 $0 账单。
- 依赖的成本分配标签需要处于激活状态（`Project`、`parallelcluster:node-type` 等）。可以用 `scripts/activate-cost-tags.sh` 或 Billing 控制台激活。
- Cost Explorer 调用按次计费，后端缓存 1 小时，前端缓存 5 分钟。

### 6.3 估算（Estimate）

- 估算 = 每个作业的 GPU 小时 × 该实例类型每 GPU 的按需单价。
- 作业来自 `sacct`（裁剪到所选期间，运行中的作业算到当前时间）。实例类型由分区名和节点名（`<队列>-dy|st-<计算资源>-<编号>`）对应到集群配置中的计算资源。
- 单价来自 AWS Pricing API（Linux、共享租户、按需），再除以 `DescribeInstanceTypes` 返回的 GPU 数，缓存 24 小时。hpc-poc 当前单价（us-west-2）：

  | 实例 | 每小时 | GPU 数 | 每 GPU 小时 |
  |---|---|---|---|
  | g6.xlarge | $0.8048 | 1 | $0.8048 |
  | g6.12xlarge | $4.6016 | 4 | $1.1504 |
  | g6e.xlarge | $1.861 | 1 | $1.861 |
  | g6e.12xlarge | $10.49264 | 4 | $2.62316 |

- 一个计算资源配置了多种实例类型时，无法确定单价，这部分 GPU 小时显示为 “unpriced”。
- 估算不含 EBS、数据传输、节点启动和空闲时间，也不含共享费用，仅用于在账单出来之前参考。

---

## 7. 从上游升级

本定制只改动了以下上游文件，其余都是新文件：

| 文件 | 改动 |
|---|---|
| `api/PclusterApiHandler.py` | `authenticate()`/`authenticated()` 增加 `group_prefixes` 参数，并把令牌声明存入 `flask.g.pcui_user_claims` |
| `app.py` | `get_identity` 接受 `team-` 组；注册 `team_quota` 蓝图；前端路由增加 `teams`、`team-usage` |
| `api/tests/test_authenticate.py` | 新增测试 |
| `frontend/src/auth/types.ts` | `UserRole` 允许 `team-*` |
| `frontend/src/components/SideBar.tsx` | 新菜单；团队负责人只显示团队菜单 |
| `frontend/src/pages/index.tsx` | 新路由；团队负责人使用精简路由并跳过 `LoadInitialState()` |
| `frontend/locales/en/strings.json` | 新增 `global.menu.teams`、`global.menu.teamUsage`、`teamQuota.*`、`teamUsage.*` |
| `README.md` | 顶部增加本文件链接 |

新文件：`api/teams/`、`api/tests/teams/`、`frontend/src/team-quota/`、`e2e/specs/teams.spec.ts`、`e2e/test-utils/teams.ts`、`scripts/build_custom_image.sh`、`README.zh-CN.md`。

升级步骤（以新上游标签 `<NEW_TAG>` 为例）：

```bash
git remote add upstream https://github.com/aws/aws-parallelcluster-ui.git   # 只需一次，只用于拉取
git fetch upstream --tags
git checkout team-quota-cost
git rebase --onto <NEW_TAG> <当前基线标签，例如 2026.10.0> team-quota-cost
# 解决冲突（通常只在上表文件中）

# 后端测试
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
# 前端测试
cd frontend && npm ci && npx jest && npm run ts-validate && npm run lint && npm run export && cd ..
git checkout -- frontend/next-env.d.ts   # build 会改写此文件

# 构建新镜像并更新堆栈
scripts/build_custom_image.sh --region us-west-2 --tag <NEW_TAG>-teams.1
aws cloudformation update-stack --region us-west-2 --stack-name pcui-custom \
  --template-url https://parallelcluster-ui-release-artifacts-us-east-1.s3.us-east-1.amazonaws.com/parallelcluster-ui.yaml \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters ParameterKey=PublicEcrImageUri,ParameterValue=<新镜像 URI> \
               ParameterKey=AdminUserEmail,UsePreviousValue=true \
               ParameterKey=Version,UsePreviousValue=true
```

如果模板新增了参数，也需要给出 `UsePreviousValue=true` 或新值。升级 ParallelCluster 大版本时，`Version` 参数改为新版本。

---

## 8. 限制与可选加固

- **按 GPU 型号限额**：Slurm 只对 `AccountingStorageTRES` 中列出的 TRES 记账和限额。hpc-poc 当前配置为 `gres/gpu`（不区分型号），所以界面只提供总 GPU 上限。如需按型号（例如 `gres/gpu:l40s`）限额，可在集群配置 `Scheduling/SlurmSettings/CustomSlurmSettings` 中把 `AccountingStorageTRES` 改为 `gres/gpu,gres/gpu:l4,gres/gpu:l40s`，通过 PCUI 编辑向导更新集群（需要停止计算队列），之后页面会自动出现对应型号的输入框。按 billing 点数限额通常已经足够，因为 g6e 的 GPU 计 2 点。
- **收窄 SendCommand 权限**：官方模板允许 PCUI 对所有带 `parallelcluster:version` 标签的实例执行 `AWS-RunShellScript`。旁路堆栈的 `PcuiSsmScopePolicy` 用显式 Deny 把它收窄到所列集群的头节点（见 2.4），无需修改官方模板。
- **Lambda 超时**：一次头节点命令最多等待 60 秒，而 API Gateway/Lambda 的超时为 30 秒。正常情况下命令几秒内完成；若集群记账数据库很慢，请求可能超时，刷新即可。
- **缓存**：头节点 ID 和集群配置缓存 5 分钟，Cost Explorer 1 小时，价格 24 小时，均为 Lambda 进程内缓存。
- **成员必须是 Linux 用户**：Slurm 允许添加尚不存在的用户名，但该用户无法登录提交作业。生命周期团队按邮箱添加成员时会自动创建 AD 用户；旧团队按用户名添加时请先在目录服务中创建用户。
- **团队名大小写**：Slurm 会把账号名转为小写，因此团队名只允许小写。

---

## 9. 测试

```bash
# 后端（Python 3.12）
python -m pytest -q
# 前端
cd frontend && npx jest && npm run ts-validate && npm run lint
# 端到端（需要已部署的环境和测试账号）
cd e2e && npm ci && npx playwright install chromium
E2E_TEST_URL=<PCUI 地址> E2E_ADMIN_EMAIL=... E2E_ADMIN_PASSWORD=... \
E2E_LEAD_EMAIL=... E2E_LEAD_PASSWORD=... E2E_TEAMB_LIMIT=6 \
npx playwright test specs/teams.spec.ts --project=chromium
```

端到端测试会修改 teamb 的上限并创建/删除测试团队 `qatest`，测试后请把上限改回原值。

---

## 10. 部署记录
部署信息（账号、地址、资源 ID）不写入仓库。部署时请在仓库之外记录以下内容：

| 项目 | 值 |
|---|---|
| PCUI 地址 | `https://<api-id>.execute-api.<区域>.amazonaws.com/pcui` |
| 堆栈名称 | `pcui-custom`（PCUI）、`pcui-custom-build`（镜像构建）、`pcui-custom-teams`（旁路堆栈） |
| 区域 | `<区域>` |
| 镜像 | `<账号>.dkr.ecr.<区域>.amazonaws.com/pcui-custom:<标签>` |
| Cognito 用户池 | `<用户池 ID>`；组 `admin`、`team-<team>`、`member-<team>` |
| 集群 | `<集群名>` |

凭据存放位置（本文件不包含任何密码或密钥）：
- 管理员初始临时密码由 Cognito 发送到管理员邮箱，首次登录时修改。
- 测试用户的密码放在仓库之外的环境文件中（权限 600），不要提交到仓库。不再需要时用 `aws cognito-idp admin-delete-user --user-pool-id <用户池 ID> --username <邮箱>` 删除。
- 应用客户端密钥：Secrets Manager 中，名称见堆栈输出 `UserPoolClientSecretName`。
- 变更审计日志：CloudWatch 日志组 `/aws/lambda/<ParallelClusterUIFun 函数名>`，过滤 `"team-quota-audit"`。

端到端测试运行方式：
```bash
cd e2e && npm ci && npx playwright install chromium
set -a; source <仓库之外的测试账号环境文件>; set +a
npx playwright test specs/teams.spec.ts specs/member-portal.spec.ts --project=chromium --reporter=list
# 修改 teamb 配额的用例需要 E2E_TEAMB_LIMIT，测完务必改回原值（例如 4）
# 生命周期用例需要 E2E_LIFECYCLE_TEAM、E2E_LIFECYCLE_MEMBER_EMAIL、E2E_LIFECYCLE_LEAD（会真实创建 AD 用户、目录、桶和 DRA）
# 成员门户用例需要 E2E_MEMBER_EMAIL、E2E_MEMBER_PASSWORD、E2E_MEMBER_TEAM
```

---

## 11. 团队生命周期（一键创建，仅管理员）
在 “Team quota” 页面点击 “Create team”。旁路堆栈部署后，表单会多出“团队负责人邮箱”和“成员”（每行一个邮箱，旁边显示推导出的集群用户名，以及“关联已有 AD 用户”复选框）。提交后弹出进度窗口，按顺序执行：

| 步骤 | 内容 |
|---|---|
| `ad` | 在 Simple AD 中创建团队组和缺少的用户（`mail` = 邮箱），加入团队组；为新建用户生成随机初始密码，存入 Secrets Manager `<Project>/user-<用户>-password`（标签 Project、Team），再用 `ds:ResetUserPassword` 设置 |
| `fsx` | 在头节点创建 `/fsx/<team>`，属组为团队 AD 组，权限 2770 |
| `s3` | 创建桶 `<Project>-<team>-<账号>-<区域>`：阻止公共访问、SSE-S3、BucketOwnerEnforced，标签 Project、CostCenter、Team |
| `dra` | FSx 数据存储库关联 `/<team>` → 桶，自动导入/导出 NEW、CHANGED、DELETED（与已有 teamA/teamB 关联相同的设置）；需要几分钟变为 AVAILABLE |
| `cognito` | 创建 `team-<team>`（负责人）和 `member-<team>`（成员）组；缺少的 PCUI 用户用临时密码创建（存入 `<Project>/user-<用户>-pcui-temp-password`，不发邮件），首次登录必须改密码 |
| `slurm` | Slurm 账号、`<team>-std` QOS、borrow 授权和成员关联 |

- **一次调用执行一步**，状态从真实资源探测得到。中途失败时点 “Retry”（或在团队列表上方的提示中点 “Resume”），已完成的步骤会被跳过；某个资源事后被删除，也会重新执行该步骤。
- 每步写一条审计日志（`lifecycle_<step>`）。密码只存在于内存变量中，不写入团队记录、日志、SSM 命令、异常或接口响应（有单元测试保证）。管理员从 Secrets Manager 取出初始密码交给用户。
- 每个团队的资源名保存在 SSM 参数 `<TeamRecordPrefix>/teams/<team>` 中，而不是临时推导。已有的 teama、teamb 通过种子记录映射到 AD 组 `teamA`、`teamB` 和 `/fsx/teamA`、`/fsx/teamB`（见 11.1）。
- **添加成员**（Manage members）：输入邮箱即可，后台同样创建缺少的 AD 用户和 PCUI 用户、设置初始密码、加入两个组并添加 Slurm 关联；重复添加是幂等的。
- **删除团队**只删除成员的 Slurm 关联、Slurm 账号和 QOS，以及 `team-<team>`、`member-<team>` 两个 Cognito 组。FSx 目录和数据、S3 桶、DRA、AD 组和用户、密码密钥、PCUI 用户全部保留，界面会列出保留的资源。有运行中作业时 Slurm 会拒绝删除。

### 11.1 部署旁路堆栈
前提：头节点安装 `adcli` 和 `openldap-clients`，并在集群配置的 `HeadNode.Iam.AdditionalIamPolicies` 中加入堆栈输出 `HeadNodeAdAdminPolicyArn`（`pcluster update-cluster`，先 `--dryrun true` 确认只改动 IAM、无需停止计算队列）。
```bash
ROLE=$(aws cloudformation describe-stack-resource --stack-name pcui-custom \
  --logical-resource-id ParallelClusterUIUserRole --query StackResourceDetail.PhysicalResourceId --output text)
aws cloudformation deploy --stack-name pcui-custom-teams \
  --template-file infrastructure/team-lifecycle/pcui-team-lifecycle.yaml \
  --capabilities CAPABILITY_NAMED_IAM --tags Component=pcui-teams \
  --parameter-overrides PcuiRoleName="$ROLE" UserPoolId=<用户池 ID> ClusterNames=<集群名> \
    Project=<项目标签> CostCenter=<成本中心> DirectoryId=<目录 ID> AdDomainName=<AD 域名> \
    AdDomainControllers=<DC1 IP>,<DC2 IP> AdAdminSecretArn=<AD 管理员密钥完整 ARN> \
    FsxFileSystemId=<文件系统 ID> EmailDomains=<客户邮箱域名>
# 已有团队的种子记录（名称与现有资源一致；teamb 同理）
aws ssm put-parameter --type String --no-overwrite --name /pcui-teams/teams/teama --value '{"version":1,"team":"teama","state":"active","ad_group":"teamA","fsx_path":"/fsx/teamA","bucket":"<teamA 桶名>","cognito_group":"team-teama","member_group":"member-teama","qos":"teama-std","dra_id":"<teamA DRA ID>","ad_users_created":[],"ad_secrets":[],"password_set":[],"cognito_users_created":[],"pcui_secrets":[],"spec":null,"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00","deleted_at":null}'
```

### 11.2 手动彻底清理（删除团队后）
删除团队会保留数据。确认数据不再需要后，按以下顺序清理（不可恢复，请先备份）：
```bash
# 1. DRA（保留文件系统中的数据）
aws fsx delete-data-repository-association --association-id <DRA ID> --no-delete-data-in-file-system
# 2. 清空并删除桶
aws s3 rm s3://<桶名> --recursive && aws s3api delete-bucket --bucket <桶名>
# 3. 在头节点删除团队目录
sudo rm -rf /fsx/<团队目录>
# 4. 在头节点删除 AD 用户和组（AD 管理员密码从 stdin 输入）
adcli delete-user <用户> --domain=<AD 域名> --login-user=Administrator --stdin-password
adcli delete-group <组> --domain=<AD 域名> --login-user=Administrator --stdin-password
# 5. 删除两个密码密钥
aws secretsmanager delete-secret --secret-id <Project>/user-<用户>-password
aws secretsmanager delete-secret --secret-id <Project>/user-<用户>-pcui-temp-password
# 6. 删除 PCUI 用户和团队记录
aws cognito-idp admin-delete-user --user-pool-id <用户池 ID> --username <邮箱>
aws ssm delete-parameter --name /pcui-teams/teams/<team>
```

---

## 12. 成员自助门户（业务人员）
集群主要由业务人员使用，运维只做辅助，所以业务人员在 PCUI 中可以自己完成日常操作。菜单“我的作业”“提交作业”“我的团队”：

1. **我的作业**：自己运行中和排队中的作业，排队原因翻译成中文（例如 `QOSGrpBilling` → “团队配额已满，可选择借用（--qos=borrow）”），可以取消；历史作业（最近 1/7/31 天）。
2. **作业详情和日志**：作业属性、stdout/stderr（默认显示末尾 16 KiB，可“加载更早内容”），以及工作目录的文件浏览。
3. **提交作业**：队列/GPU 类型（g6 = L4，g6e = L40S）、GPU 数、CPU、内存、时长，可选“借用空闲资源”（`--qos=borrow`，最长 4 小时）；脚本二选一：集群上的脚本路径，或**直接粘贴脚本内容**（≤ 64 KiB）。
4. **取消作业**：只能取消自己的作业。
5. **我的团队**：只读查看本团队配额、当前用量，以及本人和全团队的 GPU 小时与成本。

### 12.1 身份规则
- **PCUI 登录名 = 完整邮箱**，例如 `carol@example.com`。
- **AD sAMAccountName = Linux 用户 = Slurm 用户 = 邮箱 `@` 之前的部分（小写）**，例如 `carol`。登录节点 SSH/SSM、命令行 `sbatch`、`squeue` 都使用 `carol`。
- 用户名规则 `^[a-z][a-z0-9._-]{0,19}$`（sAMAccountName 最长 20 个字符）；不符合的邮箱不能创建账号。
- **冲突规则**（假设客户只有一个邮箱域名）：创建时按用户名查 AD。不存在 → 创建并写入 `mail`；存在且 `mail` 相同 → 复用；存在但 `mail` 不同 → 拒绝（409，“AD 用户 X 已属于其他邮箱”）；存在但没有 `mail`（如演示用户 alice/bob）→ 只有勾选“关联已有 AD 用户”才复用。若允许多个域名，`carol@<域名1>` 和 `carol@<域名2>` 会冲突，第二个会被拒绝。
- 服务端只从**已验证的 ID 令牌**中取邮箱（`email_verified` 必须为真，`sub` 必须与访问令牌一致），推导集群用户名；请求参数和请求体中的用户名一律忽略。

### 12.2 角色：`member-<team>`
成员组使用 `member-<team>`，而不是 `team-<team>-member`：所有 `team-*` 组都会被当作团队负责人，`team-teamc-member` 会被误认为团队 `teamc-member` 的负责人。独立前缀保证成员默认进不了负责人和管理员接口。一个人可以同时是负责人（`team-<team>`）和成员（`member-<team>`）。

### 12.3 执行模型
- 作业、日志、文件、提交、取消都在**登录节点**上以成员自己的 Linux 用户执行：`sudo -n -H -u <用户> -- /bin/bash --noprofile --norc -c <固定脚本> ...`，从不以 root 执行。Linux 文件权限和 Slurm 关联与命令行完全一致。
- 登录节点每次按标签实时查找（`parallelcluster:cluster-name=<集群>`、`parallelcluster:node-type=LoginNode`、运行中）；找不到时返回“登录节点暂不可用”（503），**不会退回到头节点**。登录节点被替换后会自动重新查找一次。
- 命令通过 `PcuiMemberExecRole` 发送，IAM 只允许它访问登录节点。
- **“我的团队”例外**：集群设置了 `PrivateData = accounts,jobs,usage,users`，普通用户看不到团队整体的配额和用量，所以这一页沿用负责人视图的数据，在头节点上执行只读命令，并且只返回成员自己的团队和本人数据。
- 管理操作（sacctmgr、adcli、创建 FSx 目录）仍在头节点执行。成员没有 Shell/终端访问；上游 SSM Shell 只对管理员开放。

### 12.4 文件与脚本规则
- 只能访问团队目录（如 `/fsx/teamA`）和自己的主目录。路径先 `realpath` 解析再判断，`../` 以及指向其他团队目录的符号链接都会被拒绝（403）。每次最多读 16 KiB，目录最多列 500 项。
- 粘贴的脚本以 gzip + base64 作为一个参数传给登录节点，在节点上解码写入 `<团队目录>/.pcui-jobs/<用户>/<随机 ID>.sbatch`（目录 0700），再 `sbatch`。脚本内容从不经过 shell 解释；编码后超过约 45,000 字符（SSM 命令 64 KB 上限）时提示改用脚本路径。
- 审计日志记录 `member_submit`、`member_cancel`、`member_read_file`，只记录作业号、路径、脚本的 SHA-256 和字节数，不记录脚本内容。
- **注意**：SSM Run Command 的命令历史（保留 30 天）中包含编码后的脚本参数，有 SSM 读取权限的管理员可以看到。

---

## 13. 生产环境加固建议（本次未部署）
- **WAF / IP 白名单**：在 API Gateway 前加 AWS WAF，限制客户办公网出口 IP（按用户决定，本次不部署）。
- **Cognito MFA**：为用户池开启 MFA（至少对 `admin` 组强制）。
- **LDAPS**：Simple AD 只提供 LDAP（389）。生产建议通过 NLB TLS 终止提供 LDAPS，或改用 AWS Managed Microsoft AD 原生 LDAPS，避免 AD 绑定密码在 VPC 内明文传输。
- **PCUI 私有部署**：使用官方模板的私有模式（VPC 终端节点），只允许内网访问。
- 定期轮换 AD 管理员密码，并清理不再需要的初始密码密钥。
