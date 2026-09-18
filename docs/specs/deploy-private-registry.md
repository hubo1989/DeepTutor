# 私有镜像构建与部署（长期方案）

源码在 GitHub Actions 构建，多架构镜像推到 **私有** GHCR 包
`ghcr.io/hubo1989/learnleader`，VPS（`usa`）用最小权限 PAT 拉取部署。

```
GitHub repo ──(Actions: docker-release)──▶ ghcr.io/hubo1989/learnleader (private)
     │  GHCR_TOKEN secret (PAT, write:packages)        │
     │                                                 │ pull
     └── git push / release                            ▼
                                          usa: docker-compose.deploy.yml
                                          (IMAGE_TAG 控制 tag，支持回滚)
```

## 凭证矩阵（最小权限）

| 位置 | 凭证 | 权限 | 用途 |
|---|---|---|---|
| Repo secret `GHCR_TOKEN` | classic PAT | `write:packages`（可加 `read:packages`，**不要**勾 repo） | CI 推镜像。**这是私有包能被 CI 推送的唯一可靠凭证**：`learnleader` 包是私有的且未链接本仓库，`GITHUB_TOKEN` 会被 403（`permission_denied: read_package`） |
| VPS `~/.docker/config.json` | classic PAT | 仅 `read:packages` | 拉镜像。不要用有 write/repo 权限的 PAT |
| 本机 gh CLI | 登录凭证 | — | 管理 release/PR；不进 CI |

创建/轮换 PAT：GitHub → Settings → Developer settings → Personal access tokens
(classic) → Generate。VPS 替换：
`ssh usa 'docker login ghcr.io -u hubo1989 -p <READ_PAT>'`
（替换后旧的 `config.json` auth 行会被覆盖；泄露时在 GitHub 侧 revoke 即可。）

## 发布流程

1. `deeptutor/__version__.py` bump（如 `1.6.2`）→ PR → merge 进 main
2. 二选一：
   - `gh release create v1.6.2 --target main --verify-tag --title ... --notes ...`
     （先 `git push origin <sha>:refs/tags/v1.6.2`）
   - Actions 页面手动触发 "Docker Release"，填 `version=1.6.2`（ref 默认 main）
3. CI 构建多架构（amd64+arm64）并推送 `<version>` + `latest`

## 部署与回滚

```bash
scripts/deploy_usa.sh            # 部署 latest
scripts/deploy_usa.sh 1.6.2      # 部署指定版本
scripts/deploy_usa.sh --rollback 1.6.1
scripts/deploy_usa.sh --status
```

脚本等容器 healthcheck 变 healthy 才退出；失败会打印最近 30 行日志。

## 故障排查

- **CI push 403 `read_package`**：`GHCR_TOKEN` secret 失效/过期 → 重新生成 PAT
  并 `gh secret set GHCR_TOKEN --repo hubo1989/DeepTutor`。
- **VPS pull 403**：VPS 的 ghcr 凭证过期 → 重新 `docker login`。
- **包设置**：user-owned 私有包的 Actions/协作授权只能网页改
  （Package settings → Manage Actions access）；走 PAT 方案后一般不需要动它。

## 关于把源码仓库也转私有

`hubo1989/DeepTutor` 目前是 **public + fork**。注意：

1. GitHub 规则：**公开仓库的 fork 不能直接改私有**。需先
   Settings → 危险区 → **Leave fork network**（脱离上游 fork 关系），之后才允许
   change visibility。
2. 脱离 fork 后失去 GitHub 的 "Sync fork" 按钮，但我们的上游同步本来就走
   `git remote add upstream HKUDS/DeepTutor` + 手动 cherry-pick（见 AGENTS.md），
   不受影响。
3. 转私有的代价：Actions 免费额度变为 2000 分钟/月（多架构镜像单次约 25 分钟，
   tests 每次 ~5 分钟，个人频率足够）；CodeRabbit 等 OSS 免费档失效。
4. 若保持 public：源码公开，但镜像（包）仍为私有——部署链路不受影响。
   两种选择都只影响源码可见性，不影响本文件的构建/部署流程。
