"""Turn an agent's write permission on or off.

Usage (from backend/, with DATABASE_URL set to the hub database):
    python -m scripts.set_agent_write --handle docs-helper --on
    python -m scripts.set_agent_write --handle docs-helper --off [--dry-run]

With --on, the agent's branch must not be main or master. Changes are audited.
"""

import argparse
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.models.agents import Agent
from app.services.audit import record_audit
from app.services.leases import write_allowed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--handle", required=True)
    switch = parser.add_mutually_exclusive_group(required=True)
    switch.add_argument("--on", action="store_true")
    switch.add_argument("--off", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    args = parser.parse_args(argv)

    engine = make_engine(get_settings().database_url)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            agent = db.scalar(select(Agent).where(Agent.handle == args.handle))
            if agent is None:
                print(f"no agent @{args.handle}", file=sys.stderr)
                return 2
            ref = agent.default_ref or "main"
            wanted = args.on
            print(f"agent @{agent.handle} on {agent.repository_full_name}@{ref}: write {agent.can_write} -> {wanted}")
            if wanted and not write_allowed(ref):
                print(f"{ref} is protected: an agent cannot write to it", file=sys.stderr)
                return 2
            if args.dry_run:
                print("dry run: nothing written")
                return 0
            agent.can_write = wanted
            record_audit(
                db,
                action="agent.write_changed",
                actor_user_id=None,
                target_type="agent",
                target_id=str(agent.id),
            )
            db.commit()
            print("updated")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
