"""Client for the knowledge base's public HTTP API.

The assistant never reaches into the KB's SQLite file or Python objects: it goes through
the same authenticated endpoints a browser or a partner system would use, so the KB stays
the single place that decides permissions, relevance thresholds and citations.
"""

from __future__ import annotations

import httpx

from ekb.errors import KBError


class KBClient:
    def __init__(self, base_url: str, tokens: dict[str, str], timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.tokens = dict(tokens)
        self.timeout = timeout

    def identities(self) -> list[str]:
        return sorted(self.tokens)

    def token(self, identity: str) -> str:
        token = self.tokens.get(identity)
        if not token:
            raise KBError("ASSISTANT.UNKNOWN_IDENTITY", 401)
        return token

    def _headers(self, identity: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(identity)}"}

    def query(self, identity: str, workspace: str, question: str, mode: str = "auto") -> dict:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.post(
                    f"{self.base_url}/query",
                    headers=self._headers(identity),
                    json={"workspace": workspace, "question": question, "mode": mode},
                )
        except httpx.HTTPError as exc:
            raise KBError("ASSISTANT.KB_UNAVAILABLE", 502) from exc
        if response.status_code >= 400:
            try:
                code = response.json().get("error", {}).get("code", "KB.ERROR")
            except ValueError:
                code = "KB.ERROR"
            raise KBError(code, response.status_code)
        return response.json()

    def documents(self, identity: str, workspace: str) -> list[dict]:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.get(
                    f"{self.base_url}/documents",
                    headers=self._headers(identity),
                    params={"workspace": workspace},
                )
        except httpx.HTTPError as exc:
            raise KBError("ASSISTANT.KB_UNAVAILABLE", 502) from exc
        if response.status_code >= 400:
            raise KBError("ASSISTANT.KB_UNAVAILABLE", 502)
        return list(response.json().get("items", []))

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=3.0, trust_env=False) as client:
                return client.get(f"{self.base_url}/health").status_code == 200
        except httpx.HTTPError:
            return False
