from .base import AgentConfig, BaseAgent
from .editor import create_editor
from .planner import create_planner
from .writer import create_writer
from .proofreader import create_proofreader
from .reviewer import create_reviewer
from .polisher import create_polisher

AGENT_CONFIGS = {
    "editor": create_editor,
    "planner": create_planner,
    "writer": create_writer,
    "proofreader": create_proofreader,
    "reviewer": create_reviewer,
    "polisher": create_polisher,
}

__all__ = [
    "AgentConfig", "BaseAgent", "AGENT_CONFIGS",
    "create_editor", "create_planner", "create_writer",
    "create_proofreader", "create_reviewer", "create_polisher",
]
