"""Create a cloud agent for a project member and attach it to a project.

Usage (from backend/, with DATABASE_URL set to the hub database):
    python -m scripts.create_cloud_agent --email you@example.com --handle claude \
        --project-slug port-natyasutra --repo sivuu96/Natyasutra --ref main [--dry-run]

The agent has no bridge device, so its runs go to the execution provider. The repository must be on
GITHUB_ALLOWED_REPOS. The owner must already be a member of the project. Writes are audited.
"""

import argparse
import re
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.models.bridge import Agent, ProjectAgent
from app.models.identity import User
from app.models.projects import Project, ProjectMembership
from app.services.audit import record_audit

HANDLE = re.compile(r"^[a-z][a-z0-9_-]{1,38}$")
MANAGE_ROLES = {"owner", "admin"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--handle", required=True, help="lowercase, used as @handle in messages")
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--repo", required=True, help="owner/name, must be on GITHUB_ALLOWED_REPOS")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--dry-run", action="store_true", help="validate and print, write nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not HANDLE.match(args.handle):
        print("handle must be 2-39 characters: lowercase letters, digits, - or _", file=sys.stderr)
        return 2
    if args.repo not in settings.github_allowed_repos:
        print(f"{args.repo} is not on GITHUB_ALLOWED_REPOS: {', '.join(settings.github_allowed_repos) or '(empty)'}", file=sys.stderr)
        return 2

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            user = db.scalar(select(User).where(User.email == args.email.lower()))
            if user is None:
                print(f"no user with email {args.email}", file=sys.stderr)
                return 2
            project = db.scalar(select(Project).where(Project.slug == args.project_slug))
            if project is None:
                print(f"no project with slug {args.project_slug}", file=sys.stderr)
                return 2
            membership = db.scalar(
                select(ProjectMembership).where(
                    ProjectMembership.project_id == project.id, ProjectMembership.user_id == user.id
                )
            )
            if membership is None or membership.role not in MANAGE_ROLES:
                print(f"{args.email} must be an owner or admin of {args.project_slug}", file=sys.stderr)
                return 2
            if db.scalar(select(Agent.id).where(Agent.handle == args.handle)) is not None:
                print(f"handle @{args.handle} is already taken", file=sys.stderr)
                return 2

            print(f"project: {project.name} ({project.slug})")
            print(f"owner:   {user.email}")
            print(f"agent:   @{args.handle} -> {args.repo}@{args.ref}")
            if args.dry_run:
                print("dry run: nothing written")
                return 0

            agent = Agent(
                owner_user_id=user.id,
                device_id=None,
                handle=args.handle,
                display_name=args.display_name or f"{args.handle} (cloud)",
                harness="claude_code",
                capabilities=[],
                repository_full_name=args.repo,
                default_ref=args.ref,
            )
            db.add(agent)
            db.flush()
            db.add(ProjectAgent(project_id=project.id, agent_id=agent.id, added_by_user_id=user.id))
            record_audit(
                db,
                action="agent.cloud_created",
                actor_user_id=user.id,
                project_id=project.id,
                target_type="agent",
                target_id=str(agent.id),
            )
            db.commit()
            print(f"created agent {agent.id}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
