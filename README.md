# NatyaSutra Collaboration Hub

A team workspace for the NatyaSutra port: projects, channels, tasks, decisions and live updates, with AI agents that answer in channels and sessions. Agents run in the cloud, so team members need nothing installed.

## How it works

- **Projects and channels.** A team has projects. Each project has channels, tasks, decisions, and an activity feed. Everything updates live for everyone in the project.
- **Agents.** An agent is a Claude Code agent with a handle (for example `@claude`). Its owner attaches it to a project. It reads a copy of its GitHub repository, and it can read PDFs and text files in it. An operator can allow an agent to push its changes to its own branch. It replies in the channel or session where it was asked, and its replies are shown as formatted Markdown.
- **Files from agents.** An agent can return up to five text files with a reply. They appear as downloads under the reply and in the recent runs list. Only people who can see the run can download them.
- **Mentions.** Type `@` in a channel to see agents and people. Mentioning an agent starts a run. Progress shows above the message box while it works, and the reply appears in the channel.
- **Sessions.** A session is a conversation with one agent. Each message continues it: the agent sees the earlier turns. Private sessions are visible only to their owner. Shared sessions can be read by the project, and the owner and project owners can send. The creator, project owners and admins can delete a session.
- **Stopping a run.** Anyone who asked, the agent's owner, or a project admin can stop a run. A run that stops responding is failed automatically after a few minutes.
- **Passwords.** People who forget their password can get a reset link by email, valid for 30 minutes.

Start with the [architecture overview](docs/architecture.md), then the component guides:

- [Backend](docs/components/backend.md): API, data model, services.
- [Frontend](docs/components/frontend.md): pages, live updates, mentions and progress.
- [Execution](docs/components/execution.md): the run queue, leases, the Cloud Run service, and the stuck-run sweeper.
- [Runtime](docs/components/runtime.md): the service that runs an agent, its model proxy, its events, and its limits.

Operating the hub:

- [Development](docs/development.md): set up and test locally.
- [Deployment](docs/deployment.md): Render, Supabase, and the Google Cloud Run service.
- [Security](docs/security.md): trust boundaries, controls, and known limits.

## Status

The hub is deployed for a small team. Working today: accounts and password reset by email, teams and projects, channels with live updates, tasks, decisions, activity and search, cloud agents that answer mentions and sessions with formatted replies, files returned by agents, session sharing and deletion, progress and stop, and `@` suggestions for agents and people.

Not yet built: notifications when someone is mentioned, resuming an agent's conversation on the same instance between turns. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) has the roadmap.

## Quick start (local)

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres      # database
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python.exe -m alembic upgrade head                # schema
.venv\Scripts\python.exe -m scripts.seed                        # optional sample users (prints a password once)
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000

# In a second terminal
cd frontend
npm install
npm run dev                                                     # http://localhost:5173
```

Local runs send agent runs to a fake, in-process provider, so nothing reaches Google Cloud. To run agents for real, see [deployment](docs/deployment.md). Tests, migrations and the local PostgreSQL setup are in [development](docs/development.md).

## Repository layout

| Path | What it is |
|---|---|
| `backend/` | FastAPI application, migrations, operator scripts, tests |
| `frontend/` | React app (Vite, TypeScript, Tailwind) |
| `runtime/` | The service that runs agent runs: the Claude Code CLI, the repository clone, the model proxy, events |
| `deploy/` | Production Dockerfile and a Compose file for running the production image locally |
| `render.yaml` | Render Blueprint for the web service |
| `docs/` | Architecture, component guides, operations and security |
