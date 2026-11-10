"""Import every model so Base.metadata is complete for Alembic and create_all."""

from app.models.agents import Agent, AgentInvocation, ProjectAgent
from app.models.audit import AuditEvent
from app.models.auth import AuthSession
from app.models.events import ProjectEvent
from app.models.execution import ExecutionOutbox, InvocationLease, RunEvent
from app.models.identity import Team, TeamMembership, User
from app.models.projects import Channel, Project, ProjectMembership
from app.models.sessions import AgentSession, AgentSessionMessage
from app.models.work import Decision, DecisionRevision, Message, Task

__all__ = [
    "Agent",
    "AgentInvocation",
    "AgentSession",
    "AgentSessionMessage",
    "AuditEvent",
    "AuthSession",
    "Channel",
    "Decision",
    "DecisionRevision",
    "ExecutionOutbox",
    "InvocationLease",
    "Message",
    "Project",
    "ProjectAgent",
    "ProjectEvent",
    "ProjectMembership",
    "RunEvent",
    "Task",
    "Team",
    "TeamMembership",
    "User",
]
