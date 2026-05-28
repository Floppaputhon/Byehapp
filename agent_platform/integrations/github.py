"""Minimal GitHub integration demonstrating the LLM-driven tool pattern.

It is intentionally scoped to a handful of high-value actions:

* ``connect_github`` — store a Personal Access Token (or a "please prompt me"
  marker if the user hasn't supplied one yet).
* ``github_list_issues`` — open issues for a repo.
* ``github_create_issue`` — open a new issue.
* ``github_disconnect`` — drop the stored token.

Tokens flow through the encrypted vault and never end up in logs or in the
LLM's context window.
"""

from __future__ import annotations

import httpx

from ..providers import ToolSpec
from .base import Integration, ToolResult


GITHUB_API = "https://api.github.com"


class GitHubIntegration(Integration):
    service = "github"
    display_name = "GitHub"

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="connect_github",
                description=(
                    "Begin connecting the user's GitHub account. Use this for "
                    "ANY phrase that asks to link GitHub — in any language. "
                    "Examples: 'connect github', 'законнектись к гитхабу', "
                    "'подруби гидхаб', 'mach verbindung mit github', "
                    "'привяжи git'. If the user already pasted a token in "
                    "this turn, pass it as 'token'; otherwise omit it and the "
                    "platform will prompt for the token."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "token": {
                            "type": "string",
                            "description": (
                                "GitHub Personal Access Token (classic or "
                                "fine-grained). Optional — if missing, the "
                                "platform will ask the user."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="github_disconnect",
                description="Remove the stored GitHub token.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolSpec(
                name="github_list_issues",
                description=(
                    "List open issues for a GitHub repository. Use for "
                    "questions like 'show my open issues in foo/bar', "
                    "'какие у меня issue в foo/bar'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in owner/name form, e.g. 'octocat/hello'.",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                    "required": ["repo"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="github_create_issue",
                description=(
                    "Create a new issue. Use for any phrasing like "
                    "'create issue in foo/bar about ...', "
                    "'заведи issue в foo/bar про ...'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string", "default": ""},
                    },
                    "required": ["repo", "title"],
                    "additionalProperties": False,
                },
            ),
        ]

    # ------------------------------------------------------------------

    async def handle(self, name: str, owner_id: int, arguments: dict) -> ToolResult:
        if name == "connect_github":
            return await self._connect(owner_id, arguments)
        if name == "github_disconnect":
            return await self._disconnect(owner_id)
        if name == "github_list_issues":
            return await self._list_issues(owner_id, arguments)
        if name == "github_create_issue":
            return await self._create_issue(owner_id, arguments)
        return ToolResult(
            ok=False,
            summary=f"GitHub: unknown tool {name}",
            error=f"unknown tool {name}",
        )

    async def _connect(self, owner_id: int, args: dict) -> ToolResult:
        token = (args.get("token") or "").strip()
        if not token:
            return ToolResult(
                ok=False,
                summary=(
                    "Чтобы привязать GitHub, пришли мне Personal Access Token "
                    "одним сообщением — я его сразу зашифрую. Создать токен: "
                    "https://github.com/settings/tokens?type=beta. Рекомендую "
                    "fine-grained PAT с правами на нужные репозитории."
                ),
                error="missing_token",
            )
        login = await self._validate_token(token)
        if not login:
            return ToolResult(
                ok=False,
                summary="GitHub отклонил этот токен. Проверь, что он не истёк.",
                error="invalid_token",
            )
        await self._store_token(
            owner_id,
            token,
            metadata={"login": login},
        )
        return ToolResult(
            ok=True,
            summary=f"GitHub привязан как @{login}.",
            data={"login": login},
        )

    async def _disconnect(self, owner_id: int) -> ToolResult:
        removed = await self._delete_token(owner_id)
        if removed:
            return ToolResult(ok=True, summary="GitHub отключён, токен удалён.")
        return ToolResult(ok=False, summary="GitHub и так не был подключён.")

    async def _list_issues(self, owner_id: int, args: dict) -> ToolResult:
        token = await self._load_token(owner_id)
        if not token:
            return ToolResult(
                ok=False,
                summary="Сначала привяжи GitHub — попроси меня «подключи гитхаб».",
                error="not_connected",
            )
        repo = (args.get("repo") or "").strip()
        if "/" not in repo:
            return ToolResult(ok=False, summary="Repo должен быть в формате owner/name.")
        limit = int(args.get("limit") or 10)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=self._headers(token),
                params={"state": "open", "per_page": limit},
            )
        if resp.status_code >= 400:
            return ToolResult(
                ok=False,
                summary=f"GitHub API: {resp.status_code} {resp.text[:200]}",
                error=str(resp.status_code),
            )
        items = [i for i in resp.json() if "pull_request" not in i]
        if not items:
            return ToolResult(ok=True, summary=f"В {repo} нет открытых issue.")
        lines = [f"Открытые issue в {repo}:"]
        for it in items[:limit]:
            lines.append(f"#{it['number']} — {it['title']}  → {it['html_url']}")
        return ToolResult(
            ok=True,
            summary="\n".join(lines),
            data={"issues": items[:limit]},
        )

    async def _create_issue(self, owner_id: int, args: dict) -> ToolResult:
        token = await self._load_token(owner_id)
        if not token:
            return ToolResult(
                ok=False,
                summary="Сначала привяжи GitHub.",
                error="not_connected",
            )
        repo = (args.get("repo") or "").strip()
        title = (args.get("title") or "").strip()
        body = args.get("body") or ""
        if "/" not in repo or not title:
            return ToolResult(
                ok=False,
                summary="Нужны repo (owner/name) и непустой title.",
            )
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=self._headers(token),
                json={"title": title, "body": body},
            )
        if resp.status_code >= 400:
            return ToolResult(
                ok=False,
                summary=f"GitHub API: {resp.status_code} {resp.text[:200]}",
                error=str(resp.status_code),
            )
        issue = resp.json()
        return ToolResult(
            ok=True,
            summary=f"Создал issue #{issue['number']}: {issue['html_url']}",
            data={"issue": issue},
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-platform/0.1",
        }

    async def _validate_token(self, token: str) -> str | None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/user", headers=self._headers(token)
            )
        if resp.status_code != 200:
            return None
        return resp.json().get("login")
