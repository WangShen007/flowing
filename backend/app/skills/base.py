from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, Field


class SkillContext(BaseModel):
    session_id: str
    user_id: str
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillResult(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] | None = None
    need_continue: bool = False


class BaseSkill(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "User input"}},
            "required": ["query"],
        }

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        raise NotImplementedError(f"Skill {self.name} does not implement execute()")

    async def execute_stream(
        self,
        context: SkillContext,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError(f"Skill {self.name} does not implement execute_stream()")
        yield {}

    async def validate(self, **kwargs: Any) -> bool:
        return True

    def to_tool_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

