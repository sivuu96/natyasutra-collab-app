# Deployment

The production hub is three hosted pieces, plus the runtime image that agent runs use:

| Piece | Where | What it does |
|---|---|---|
| Hub: API, web app, WebSocket, worker | Render web service, from `deploy/Dockerfile` | Serves everything, runs migrations on start, starts agent runs. |
| Database | Supabase PostgreSQL | All state. |
| Agent runs | Google Cloud Run Jobs, project `natyasutra-agents`, region `us-west1` | One container per run, from the runtime image. |
| Runtime image | Artifact Registry, repository `agent-runtime` | The image the job runs. |
| Secrets | Google Secret Manager | Anthropic API key and GitHub App private key. |

Serving the UI from the same address as the API keeps sign-in and live updates simple: no cross-origin cookies. TLS certificates are automatic on Render. Secure cookies are on by default in production.

The steps are in order: database, Render, first accounts, Google Cloud, then operation. Do Google Cloud before you expect agents to answer.

## Cost and limits

Render's free plan and Supabase's free plan cost nothing, but they have limits that matter:

- **Render web service (free):** spins down after 15 minutes without inbound traffic. Waking it takes about a minute. The disk is temporary; the hub keeps everything in PostgreSQL, so that is safe. Monthly free instance hours are limited, and one always-on instance uses most of them. Check Render's current plan limits before relying on them.
- **Supabase (free):** projects **pause after a week of inactivity**. Data is kept, but the hub is down until someone restores the project in the dashboard. Storage is capped, and there are **no automatic backups** (see "Backups" below).
- **Google Cloud:** agent runs are billed by run time, and the image and secrets have small storage charges. Check GCP's pricing for your region and set a budget alert on the project.

Plan limits change. Check the Render, Supabase and Google Cloud pricing pages before you rely on them.

## Database (Supabase)

Create a Supabase PostgreSQL project in the region closest to the Render service (Render uses Oregon, so choose **us-west-2**). Keep the database password somewhere safe.

**Connection string.** Use the **Session pooler** URI from Supabase (**Connect > Session pooler**):

`postgresql://postgres.<project-ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres?sslmode=require`

- Use the **Session pooler on port 5432**. The direct connection (`db.<project-ref>.supabase.co`) is IPv6-only, and Render cannot reach it.
- Do not use the Transaction pooler (port 6543) unless you also set `prepare_threshold=None` in the app. The app does not do this today.
- If the password contains special characters such as `@`, URL-encode them (`@` becomes `%40`).
- Set this value as `DATABASE_URL` in Render (**Environment**). Never commit it.

**Row-level security (required).** Supabase's Data API exposes tables in the `public` schema to the `anon` and `authenticated` roles by default. The hub's tables are in `public`. After the first deploy has created them, run this in the Supabase SQL editor as the `postgres` role:

```sql
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
  END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
```

The hub connects as `postgres`, which keeps access. Confirm that `set role anon; select count(*) from users;` is refused.

**Moving an existing database.** Dump with `pg_dump -Fc --no-owner --no-privileges`, restore with `pg_restore --no-owner --no-privileges --exit-on-error` into the new project, compare row counts and `alembic_version`, apply the security SQL above, and update `DATABASE_URL`. Use a PostgreSQL 17 client or newer for the dump, to match the server.

**Backups.** The free plan has no automatic backups. Export with `pg_dump` on a schedule and keep the files somewhere you control. Paid plans include daily backups.

## 1. Deploy the hub on Render

1. In Render, choose **New > Blueprint** and select this repository. Render reads `render.yaml`.
2. Set the variables marked "set in the dashboard" (see the configuration reference below). At least `DATABASE_URL` and the Google Cloud settings.
3. Deploy. Migrations run before the server starts. If a migration fails, the new version does not start, and Render keeps the previous version serving. Check the deploy logs.
4. Open `https://<service>.onrender.com/health` (should say `ok`) and `/ready` (should say the database is up).

Every push to `main` deploys, because `autoDeploy` is on.

## 2. Create the first accounts

Registration is closed in the deployed configuration (`ALLOW_REGISTRATION=false`). Create accounts from the server:

1. In the Render dashboard, open the web service, then **Shell**.
2. Run, once per person:

   ```sh
   python -m scripts.manage create-user --email you@example.com --name "Your Name"
   ```

   It asks for a password twice, without echoing it.
3. Sign in, create a team and a project, and add teammates by their email. They must already have an account.

To reopen registration, set `ALLOW_REGISTRATION` to `true` in Render, and set it back to `false` when you're done.

Forgotten password:

```sh
python -m scripts.manage set-password --email you@example.com
```

This ends every session for that account.

## 3. Google Cloud setup (agent runs)

Agent runs need this section. Without it, a mention stays queued until the sweeper fails it.

The names below are the ones the hub expects by default. Change them only together with the configuration.

### 3.1 Project and APIs

Use a Google Cloud project, for example `natyasutra-agents`. Enable these APIs: Cloud Run, Artifact Registry, Secret Manager, IAM, and Cloud Logging.

Set a budget alert on the project before you start, so a runaway run cannot cost much unnoticed.

### 3.2 Artifact Registry and the runtime image

```sh
gcloud artifacts repositories create agent-runtime --repository-format=docker --location=us-west1
gcloud auth configure-docker us-west1-docker.pkg.dev
docker build -f runtime/Dockerfile -t us-west1-docker.pkg.dev/<project>/agent-runtime/collab-agent-runtime:<tag> .
docker push us-west1-docker.pkg.dev/<project>/agent-runtime/collab-agent-runtime:<tag>
```

Build from the repository root. Note the image **digest** that `docker push` prints (`sha256:...`). Use the digest when you create or update the job, so the job runs exactly the image you tested. A placeholder digest in the job command will fail.

### 3.3 Secrets

Create two secrets in Secret Manager:

| Secret | Contents |
|---|---|
| `anthropic-api-key` | An Anthropic API key for the team's usage. Not a personal key. |
| `github-app-key` | The private key (PEM) of the GitHub App. |

Never put either value in a file in the repository, in Render, or in chat.

### 3.4 Service accounts and roles

Two service accounts, each with the least access it needs:

| Account | Roles | Used by |
|---|---|---|
| `agent-runtime` | `roles/logging.logWriter`; `roles/secretmanager.secretAccessor` on **only** `anthropic-api-key` and `github-app-key` | The running job |
| `agent-dispatcher` | `roles/run.developer` on the job; `roles/iam.serviceAccountUser` on `agent-runtime` | The hub, to start and stop runs |

The default compute service account should have no roles on this project.

### 3.5 The job

Create the job with the runtime account, the pinned image digest, and the settings below. The task timeout must be longer than the runtime's own limit (25 minutes), and retries must be off, because a retried run would start from a lease that is already used.

```sh
gcloud run jobs create collab-agent-runtime \
  --region us-west1 \
  --image us-west1-docker.pkg.dev/<project>/agent-runtime/collab-agent-runtime@sha256:<digest> \
  --service-account agent-runtime@<project>.iam.gserviceaccount.com \
  --task-timeout 1800s \
  --max-retries 0 \
  --set-env-vars GCP_PROJECT_ID=<project>,GITHUB_APP_ID=<app-id>,GITHUB_INSTALLATION_ID=<installation-id>
```

Check these flags against your installed `gcloud` version (`gcloud run jobs create --help`). Update the image later with `gcloud run jobs update ... --image <...>@sha256:<new-digest>`.

### 3.6 The dispatcher key

The hub needs a key for `agent-dispatcher` to start runs.

1. Create a key for `agent-dispatcher` and download the JSON file.
2. Encode it on one line, for example with `base64 -w0 key.json` on Linux, or in PowerShell `[Convert]::ToBase64String([IO.File]::ReadAllBytes("key.json"))`.
3. Set the result as `GCP_DISPATCHER_KEY` in Render.
4. Delete the JSON file. Keep no copy outside Render.

If your organisation blocks key creation (the `iam.disableServiceAccountKeyCreation` policy), ask an administrator for an exception for this one account, or move the hub to a platform that can use workload identity.

### 3.7 GitHub App

Create a GitHub App with **Repository permissions: Contents, read-only**, and no other permissions. Install it only on the repositories agents should read. Note the App ID and the installation ID, set them on the job (step 3.5), and store the private key as `github-app-key` (step 3.3).

Then set `GITHUB_ALLOWED_REPOS` in Render to the same repositories, as `owner/name` separated by commas. The hub refuses a run for any other repository.

### 3.8 Create an agent

An operator creates an agent for a project owner or admin. The owner must already be a member of the project.

```sh
python -m scripts.create_cloud_agent --email owner@example.com --handle claude \
  --project-slug <project-slug> --repo owner/name --ref main --dry-run
python -m scripts.create_cloud_agent --email owner@example.com --handle claude \
  --project-slug <project-slug> --repo owner/name --ref main
```

Run it from the Render shell. The first command checks everything and writes nothing. The second creates the agent and attaches it to the project. The owner can then mention `@claude` in a channel or start a session with it.

## 4. Configuration reference

Set these in Render (**Environment**), or in `render.yaml` where marked.

| Variable | Where | Value |
|---|---|---|
| `DATABASE_URL` | Dashboard (secret) | Supabase Session pooler URL. |
| `RUNTIME_TOKEN_SECRET` | Generated by `render.yaml` | Signs the tokens that agent runs use to call the hub. Old deployments may still have `BRIDGE_TOKEN_SECRET`; it is still read. |
| `EXECUTION_PROVIDER` | `render.yaml`: `cloud_run` | Starts agent runs on Cloud Run. |
| `GCP_PROJECT_ID` | Dashboard | `natyasutra-agents` or your project. |
| `GCP_DISPATCHER_KEY` | Dashboard (secret) | Base64 of the dispatcher key (step 3.6). |
| `GCP_REGION` | Optional | Default `us-west1`. |
| `GCP_JOB_NAME` | Optional | Default `collab-agent-runtime`. |
| `GITHUB_ALLOWED_REPOS` | Dashboard | `owner/name,owner/other`. |
| `HUB_BASE_URL` | Optional | Defaults to Render's external URL. |
| `ALLOW_REGISTRATION` | `render.yaml`: `false` | Sign-up is closed by default. |
| `LOG_LEVEL` | `render.yaml`: `INFO` | Log level. |

Google Cloud settings that are not on Render: the job's image, service accounts, timeouts and environment (step 3.5), and the secrets in Secret Manager.

## 5. Operating the hub

**Watching runs.** In Render, the service logs show each request. In Google Cloud, list recent executions with `gcloud run jobs executions list --job collab-agent-runtime --region us-west1` and read one execution's logs in Cloud Logging. A run that never starts is failed by the hub after 15 minutes, with "The agent did not start in time."

**Updating the hub.** Push to `main`. Migrations run before the new version starts.

**Updating the runtime.** Build a new image, push it, and update the job to the new digest. Test with a mention in a test channel before you tell the team.

**Rotating secrets.**
- Anthropic key: add a new version of `anthropic-api-key` in Secret Manager. The next run reads the newest version. Disable the old one when you are sure.
- GitHub App key: generate a new private key in GitHub, add it as a new version of `github-app-key`, then delete the old key in GitHub.
- Dispatcher key: create a new key, set `GCP_DISPATCHER_KEY` in Render, then delete the old key in Google Cloud.
- Runtime token secret: changing it invalidates tokens of runs in progress, which then fail. Do it between runs.

**Backups.** Run `pg_dump` against the Supabase Session pooler on a schedule (for example weekly) and keep the files somewhere you control. Test a restore into a scratch database occasionally.

**Supabase pauses.** If the hub returns errors and the Supabase project shows as paused, restore it in the Supabase dashboard. Data is kept.

## 6. Run the production image on your own machine

The Compose file runs the same image as production, against a local PostgreSQL, so you can check a release before deploying it.

```powershell
# deploy/.env must contain RUNTIME_TOKEN_SECRET=<32+ random characters>
docker compose -f deploy/docker-compose.yml up --build
# open http://localhost:8080
```

Use `EXECUTION_PROVIDER=fake` here (the default), so no run reaches Google Cloud.

## Known limits

- **One hub instance.** The rate limiter, the realtime fan-out and the execution worker run in process. Two instances would fan out events to only some subscribers and could start a run twice. Scaling out needs a shared store and a message bus first (see [security](security.md#10-known-limitations)).
- **Starting a run is not idempotent at the provider.** `jobs:run` has no idempotency key, so a timed-out call can start a second execution. That execution's lease exchange is refused, so it exits without doing any work. See [execution](components/execution.md#cloud-run-provider).
- **Sessions have a memory cap.** Earlier turns are replayed up to 40 turns and 20,000 characters. Longer sessions lose their oldest turns. See [backend](components/backend.md#sessions).
- **Free plans sleep.** Render spins down after 15 minutes without traffic, and Supabase pauses after a week. Both need a manual or scheduled wake-up if the team relies on the hub being always available.
