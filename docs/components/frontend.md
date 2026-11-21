# Frontend

The frontend is the web app people use. It is a React single-page app, built with Vite and TypeScript, styled with Tailwind. The hub serves the built app from the same address as the API, so cookies and the live connection need no cross-site setup.

Source: `frontend/`. Build: `npm run build` (type-check, then Vite build). Tests: `npm test` (Vitest).

## Pages and routes

| Route | Page | What it shows |
|---|---|---|
| `/login` | `LoginPage` | Sign in. Links to registration and "Forgot password?". |
| `/register` | `RegisterPage` | Create an account, when registration is open. |
| `/forgot-password` | `ForgotPasswordPage` | Asks for an email and sends a reset link. The answer is the same whether or not the account exists. |
| `/reset-password?token=…` | `ResetPasswordPage` | Sets a new password with a reset link. Sign-in sessions end after a reset. |
| `/` | `ProjectsPage` | Teams and projects you belong to. |
| `/projects/:id/discussions` | `DiscussionsView` | Channels, messages, the composer, `@` suggestions, agent progress, and agent replies rendered as Markdown. |
| `/projects/:id/tasks` | `TasksView` | Tasks, with status and priority. |
| `/projects/:id/decisions` | `DecisionsView` | Decisions and their revisions. |
| `/projects/:id/agents` | `AgentsView` | Agents attached to the project, your agents to offer, and recent runs with their returned files and a Cancel button. |
| `/projects/:id/sessions` | `SessionsView` | Sessions with an agent: transcript, composer, sharing, closing, deleting, and files. |
| `/projects/:id/activity` | `ActivityView` | Project activity and the audit log. |
| `/projects/:id/people` | `PeopleView` | Members, roles, and adding people by email with suggestions. |
| `/projects/:id/search` | `SearchView` | Search across messages, tasks and decisions. |

Every project page sits inside `ProjectWorkspace`, which owns the live connection and gives each page the project and the person's role.

## Who sees what

The page shows only what the hub allows, and the hub enforces every rule again. Hiding a button is never the security boundary.

- **Composer in a session:** shown to the session's owner and to project owners of a shared session. The hub's `can_send` flag decides it.
- **Delete a session:** shown when the hub's `can_delete` flag is true: the session's creator, a project owner, or a project admin.
- **Close and sharing buttons:** shown to the session's owner only.
- **Agent offers and cancel:** the rules in `agents/permissions.ts` mirror the hub's.

## Agent replies

Agent replies are shown as Markdown (`components/Markdown.tsx`), in sessions and in channels. Bold, italics, headings, lists, tables, quotes and code render as formatting, not as symbols.

- Raw HTML in a reply is not rendered.
- Images show as their alt text, so a reply cannot load a remote picture.
- Links open in a new tab, and unsafe link targets such as `javascript:` are removed.

Messages from people are shown as plain text, with mentions highlighted.

## Files from runs

When a run returns files, they appear as download links under the reply (`components/RunFiles.tsx`), and in the recent runs list. Each link goes to `/api/v1/projects/…/invocations/…/files/…`, which the hub sends as a plain-text attachment.

## Live updates

`ProjectWorkspace` opens one WebSocket per project (`api/realtime.ts`). It reconnects with a backoff, sends its last sequence number so the hub can replay what it missed, and shows a notice if the hub refuses the connection.

Pages subscribe to events and refresh what they show:
- Agent events refresh the agent and run lists.
- Message events refresh the channel.
- Session events refresh the session list and the open transcript.
- Streamed agent text (`agent.output.delta`) is shown as it arrives, in `sessions/streaming.ts`, and replaced by the saved reply when it lands.

## Mentions

Typing `@` in the composer opens a list of people and agents in the project (`discussions/mentions.ts`). The list is built from the attached agents and the members.

- `activeMention` finds the `@` being typed at the caret.
- `suggestMentions(candidates, query)` ranks agents first, then people, by handle prefix. It returns at most six entries.
- `applyMention` inserts the chosen handle.

While an agent works, a progress line appears above the message box (`discussions/runProgress.ts`): `@claude: Queued…`, then the stage the runtime reports. Each line has a Stop button.

## Project data and state

- `project/workspaceContext.tsx` provides the project, the person's role, and helpers such as `canWrite`.
- `auth/AuthContext.tsx` holds the signed-in user and the sign-in and sign-out actions.
- `api/client.ts` makes the requests. It sends the cookie, echoes the CSRF token on writes, and turns error responses into readable messages. `runFileUrl` builds the download address for a run's file.
- `api/types.ts` has the shapes the API returns, such as `AgentSession` (with `can_send` and `can_delete`), `Invocation` (with `files`) and `SessionMessage`.

## Components

| File | Purpose |
|---|---|
| `components/ui.tsx` | Buttons, cards, inputs, labels, errors, and time formatting. |
| `components/Markdown.tsx` | Renders an agent's reply as safe Markdown. |
| `components/RunFiles.tsx` | Download links for a run's returned files. |

## Tests

- `frontend/src/**/*.test.ts(x)` cover the mention rules, run progress, streaming text, the permission rules, the client, the realtime connection, and the login and progress flows.
- These tests do not run on Node 26 with Vitest 4 yet (a known issue; use Node 22 LTS). The type check (`npx tsc --noEmit`) and the production build do run.
