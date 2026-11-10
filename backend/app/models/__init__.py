"""Import every model so Base.metadata is complete for Alembic and create_all."""

from app.models.audit import AuditEvent
from app.models.auth import AuthSession
from app.models.bridge import Agent, AgentApproval, AgentEvent, AgentInvocation, BridgeDevice, PairingCode, ProjectAgent
from app.models.events import ProjectEvent
from app.models.execution import ExecutionOutbox
from app.models.identity import Team, TeamMembership, User
from app.models.projects import Channel, Project, ProjectMembership
from app.models.sessions import AgentSession, AgentSessionMessage
from app.models.work import Decision, DecisionRevision, Message, Task

__all__ = [
    "Agent",
    "AgentApproval",
    "AgentEvent",
    "AgentInvocation",
    "AgentSession",
    "AgentSessionMessage",
    "AuditEvent",
    "AuthSession",
    "BridgeDevice",
    "Channel",
    "Decision",
    "DecisionRevision",
    "ExecutionOutbox",
    "Message",
    "PairingCode",
    "Project",
    "ProjectAgent",
    "ProjectEvent",
    "ProjectMembership",
    "Task",
    "Team",
    "TeamMembership",
    "User",
]
