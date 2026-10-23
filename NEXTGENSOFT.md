# NextGenSoft fork notes

Fork of [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) (AGPL-3.0),
pinned at upstream `dadd802d634a74c7abebdc288e0f8a41750e977e` (2026-10-22), served at
https://clips.nextgensoft.co for invited NextGenSoft clients. Sign-up is disabled.

## Deploy

Backend first, then frontend:

```bash
kamal deploy -c config/deploy.api.yml
kamal deploy -c config/deploy.yml
```

## Upgrade from upstream

```bash
git fetch upstream && git merge upstream/main
```

Apply any new `frontend/prisma/migrations` or `backend/migrations` SQL **by hand against
the shared db before deploying** — never run `prisma migrate deploy` against this db.
`backend/src/migrations/sql/*.sql` applies itself automatically at boot (`init_db`), so
backend-side migrations need no manual step.

## Account commands

```bash
# Admin
kamal app exec -c config/deploy.yml --reuse "node scripts/create-user.mjs <email> '<Name>' '<password>' --admin"
# Client (re-running an existing email resets its password)
kamal app exec -c config/deploy.yml --reuse "node scripts/create-user.mjs <email> '<Name>' '<temp password>'"
```

## Resource caps and why

Shared axveer host runs ~50 other apps — caps keep this one from starving them:

- Worker: `--cpus=2 --memory=3g`, `WORKER_MAX_JOBS=1` (video processing is the only
  CPU/memory-heavy path; one job at a time bounds both).
- Backend web: `--memory=1g` (thin API layer, no media processing).
- Frontend: `--memory=512m` (Next.js standalone).
- Redis: `--memory=128m` (job progress/queue only, no durable data).
- bgutil: `--memory=256m` (YouTube PO token provider, lightweight).

Media is not stored on the host — it lives in Cloudflare R2, with browsers uploading to
and playing from R2 directly via presigned URLs. `/srv/clips-nextgensoft-co` holds job
temp files only and is not backed up.

## Expected processing time

TBD — recorded after the Task 7 acceptance run (30-minute source timing baseline).
