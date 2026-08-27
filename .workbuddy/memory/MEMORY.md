# DeepTutor — Long-term Project Notes

## VPS deployment (host alias `usa` = 100.126.160.20, dir /home/hubo/deeptutor)
- Production image: `ghcr.io/hubo1989/learnleader:latest` (renamed from `deeptutor` on 2026-08-27; multi-platform amd64+arm64). `deeptutor` package now legacy — delete once no longer referenced.
- The running `deeptutor` container topology: network `deeptutor_deeptutor`, host ports `8002`(backend) + `3782`(frontend), `./data:/app/data` bind mount. `deeptutor-pocketbase` shares that network.
- Redeploy uses `/home/hubo/deeptutor/docker-compose.deploy.yml` (mirrors running container, swaps only image). Do NOT use the repo's `docker-compose.ghcr.yml` on the VPS (it's an outdated partial-mount/`8001` version) or base `docker-compose.yml` (would build stale local code).
- VPS ghcr auth: if pull is denied, re-login with a valid `read:packages` token (`gh auth token | ssh usa "docker login ghcr.io -u hubo1989 --password-stdin"`). GitHub API cannot change container-package visibility (UI only).
- Future redeploy: `cd /home/hubo/deeptutor && docker compose -f docker-compose.deploy.yml pull && docker compose -f docker-compose.deploy.yml up -d`
