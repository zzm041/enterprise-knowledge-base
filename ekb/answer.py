from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["answered", "insufficient_knowledge"]
    answer: str = Field(max_length=6000)
    citation_chunk_ids: list[int]


class Extractive:
    mode = "extractive"

    def generate(self, question, evidence):
        if not evidence:
            return {
                "status": "insufficient_knowledge",
                "answer": "",
                "citation_chunk_ids": [],
            }
        return {
            "status": "answered",
            "answer": evidence[0]["text"],
            "citation_chunk_ids": [evidence[0]["id"]],
        }
