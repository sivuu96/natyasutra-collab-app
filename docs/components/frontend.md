# Frontend

The frontend is the web app people use. It is a React single-page app (React 19, Vite, TypeScript, Tailwind), built into `frontend/dist` and served by the hub from the same address as the API. Same origin means cookies, CSRF and WebSockets work without extra configuration.

Source: `frontend/src/`. Tests: Vitest with Testing Library and jsdom.

## Routes

| Route | Page | What it shows |
|---|---|---|
| `/login`, `/register` | `LoginPage`, `RegisterPage` | Sign-in and sign-up (sign-up is open only when the hub allows it). |
| `/` | `ProjectsPage` | Your teams and projects. |
| `/projects/:id/discussions` | `DiscussionsView` | Channels, messages, the composer, `@` suggestions, agent progress. |
| `/projects/:id/tasks` | `TasksView` | Tasks: create, change status, assign. |
| `/projects/:id/decisions` | `DecisionsView` | Decisions with their revisions. |
| `/projects/:id/agents` | `AgentsView` | Agents attached to the project, your agents, and recent runs with Cancel. |
| `/projects/:id/sessions` | `SessionsView` | Sessions with an agent: transcript, turns, sharing, closing. |
| `/projects/:id/activity` | `ActivityView` | The project's activity feed. |
| `/projects/:id/people` | `PeopleView` | Project members and their roles. |
| `/projects/:id/search` | `SearchView` | Search across messages, tasks and decisions. |

`ProjectWorkspace` is the shell for a project: the tabs, the connection status, and the workspace context that every page reads (`project/workspaceContext.tsx`). It provides the current role, `canWrite`, and `subscribe` for live events.

## Talking to the hub

- `api/client.ts` wraps `fetch`. It sends cookies, adds the `X-CSRF-Token` header from the `ns_csrf` cookie on writes, and turns error responses into messages. Pages call `api<T>(path, options)`.
- `api/realtime.ts` opens one WebSocket per project at `/api/v1/ws/projects/{id}?since=<sequence>`. It reconnects with backoff and asks for replay from the last sequence it saw. A close with code 1008 means not authorised, so it stops retrying.
- `ProjectWorkspace` owns the connection. Pages subscribe to events and refresh the data they show. Agent events refresh the agent and run lists. Message events refresh the channel.

Types for every API response are in `api/types.ts`. Keep them in step with the backend schemas.

## Mentions and `@` suggestions (`discussions/mentions.ts`)

Typing `@` in the composer opens a list of people and agents in the project. The list is built from the attached agents and the members.

- `activeMention(text, caret)` finds the `@` word the caret is in, if any.
- `suggestMentions(candidates, query)` ranks agents first, then people, by handle prefix. It returns at most six entries.
- `applyMention` replaces the word with `@handle`, adds a space only when none follows, and moves the caret after it.

Keyboard: arrow keys move through the list, Enter or Tab picks, Escape closes it. Mentions do not notify anyone yet.

## Agent progress (`discussions/runProgress.ts`)

Files an agent returns appear as download links under its reply (sessions) and in the recent runs list (agents). Each link opens the file as a plain-text attachment.

While an agent works, a progress line appears above the message box: `@claude: Queued…`, then the stage the runtime reports (for example `Preparing the repository…`, `Running the agent…`, `Writing the reply…`). Each line has a Stop button.

- `applyRunEvent(state, event)` turns `agent.invocation.created`, `agent.invocation.progress`, and the final events into the map of active runs. Final events remove the run.
- `activeRuns(state, now)` hides a run that has not reported for six minutes. The page refreshes `now` every 30 seconds, so a run that stopped reporting disappears on its own. The hub also fails such a run; see [Execution](execution.md).
- The Stop button calls `POST /projects/{id}/invocations/{id}/cancel`.

## Sessions (`pages/SessionsView.tsx`)

The left column lists the sessions you can see, and the new-session form creates one with a chosen agent, a title, and a visibility. The centre shows the transcript, streaming text while the agent replies, and the composer for the owner. Members of a shared session can read the transcript and watch it live, but the composer is hidden for them. The right column explains how sessions work.

A session accepts one turn at a time. The page disables the composer while a turn is active, and it shows a Cancel turn button for the active run.

## Components

`components/ui.tsx` holds the shared controls: `Button`, `Card`, `Input`, `TextArea` (it forwards refs, so the composer can be focused), `Select`, `Label`, `EmptyState`, `ErrorText`, `formatTime` and `errorMessage`. Use these rather than writing new styles.

## Permissions in the UI

`agents/permissions.ts` mirrors the hub's agent rules so the UI can hide buttons that would fail: who may offer an agent to a project, and who may cancel a run. The hub enforces every rule again. Hiding a button is never the security boundary.

## Tests

```powershell
cd frontend
npm test              # Vitest
npx tsc --noEmit -p . # type check
npm run build         # type check and production bundle
```

Tests cover the API client, the realtime client, the login flow, mention ranking and insertion, run progress, the session stream, and the progress line on the discussions page.
