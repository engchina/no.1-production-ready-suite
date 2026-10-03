"""公開 GitHub カタログを配布物から解決する。配布コード・hook・shell は実行しない。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import PurePosixPath
from time import monotonic
from typing import Any
from urllib.parse import ParseResult, quote, urlparse

import httpx
from pr_backend_core.internal_http import http_client_options
from pydantic import ValidationError

from app.features.agent.config import McpConnectionConfig
from app.features.agent.plugins import (
    MarketplaceEntry,
    MarketplaceListing,
    PluginImportPreview,
    PluginManifest,
    PluginResource,
)
from app.features.agent.skills import SkillMcpRequirement
from app.features.agent.skills_loader import _skill_from_mapping, _split_frontmatter

MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 12 * MAX_FILE_BYTES
MAX_REQUESTS = 80
MAX_REFERENCE_BYTES = 4 * MAX_FILE_BYTES
MAX_SKILLS = 40
IMPORT_TIMEOUT_SECONDS = 120.0
COMPONENTS_NOT_RUN = (
    "commands",
    "agents",
    "hooks",
    "lspServers",
    "outputStyles",
    "monitors",
    "dependencies",
    "userConfig",
    "settings",
    "channels",
    "experimental",
)
BASE_WARNING = (
    "外部 Skill の指示と参照文書を導入します。"
    "元製品の実行機能や業務での動作を確認したものではありません。"
)
LICENSE_WARNING = "配布元の利用条件は別途適用されます。導入前に利用できる範囲を確認してください。"


def external_id(kind: str, *parts: str) -> str:
    """更新で変わらず、配布元・plugin・path を区別する namespace。"""
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:20]
    label = re.sub(r"[^a-zA-Z0-9_-]+", "-", parts[-1]).strip("-")[:40] or "item"
    return f"external-{kind}-{label}-{digest}"


def safe_path(value: str, *, allow_root: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(c in value for c in ("\\", ":", "\x00", "?", "#"))
    ):
        raise ValueError("配布物の path が不正です。")
    if value.startswith("/") or ".." in value.split("/"):
        raise ValueError("配布物の root 外を参照する path は導入できません。")
    path = str(PurePosixPath(value))
    if path == "." and not allow_root:
        raise ValueError("配布物の path が空です。")
    return "" if path == "." else path


def _parse_url(value: str) -> ParseResult:
    try:
        return urlparse(value)
    except ValueError as exc:
        raise ValueError("配布物の URL の形式が不正です。") from exc


def github_repository(value: str) -> str:
    parsed = _parse_url(value)
    if parsed.scheme:
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("配布先は公開 GitHub の HTTPS repository のみ対応しています。")
        value = parsed.path.strip("/")
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(
        p in (".", "..") for p in value.split("/")
    ):
        raise ValueError("公開 GitHub の repository を指定してください。")
    return value


def catalog_origin(url: str) -> tuple[str, str, str] | None:
    parsed = _parse_url(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "raw.githubusercontent.com"
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 4:
        return None
    repo = github_repository("/".join(parts[:2]))
    # 現在の JSON URL の形式。slash を含む ref は source.ref で扱う。
    return repo, parts[2], safe_path("/".join(parts[3:]))


class DownloadSession:
    """HTTP の件数・本文サイズ・全体期限を、導入単位で制限する。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.deadline = monotonic() + timeout
        self.requests = 0
        self.bytes = 0
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=False, **http_client_options(url)
        )

    def __enter__(self) -> DownloadSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def read(
        self, url: str, *, limit: int = MAX_FILE_BYTES, accept: str = "application/json"
    ) -> bytes:
        remaining = self.deadline - monotonic()
        self.requests += 1
        if remaining <= 0 or self.requests > MAX_REQUESTS:
            raise ValueError("配布物の取得期限または取得件数の上限を超えました。")
        data = bytearray()
        with self.client.stream(
            "GET", url, timeout=remaining, headers={"accept": accept}
        ) as response:
            if response.status_code in (401, 403, 404):
                raise ValueError(
                    "公開配布物を取得できません。取得制限・公開状態・path を確認してください。"
                )
            if 300 <= response.status_code < 400:
                raise ValueError(
                    "配布物の転送先を自動で取得しません。公開配布元の URL を確認してください。"
                )
            response.raise_for_status()
            for chunk in response.iter_bytes():
                self.bytes += len(chunk)
                data.extend(chunk)
                if len(data) > limit or self.bytes > MAX_TOTAL_BYTES or monotonic() > self.deadline:
                    raise ValueError("配布物のサイズまたは取得時間の上限を超えました。")
        return bytes(data)

    def json(self, url: str, *, limit: int = MAX_FILE_BYTES) -> dict[str, Any]:
        try:
            value = json.loads(self.read(url, limit=limit))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("配布元が有効な JSON の一覧を返していません。") from exc
        if not isinstance(value, dict):
            raise ValueError("配布元の JSON は object である必要があります。")
        return value


class GithubSnapshot:
    def __init__(self, session: DownloadSession, repo: str, ref: str = "HEAD") -> None:
        self.session = session
        self.repo = github_repository(repo)
        if not isinstance(ref, str) or not ref or len(ref) > 200 or any(c in ref for c in "\x00?#"):
            raise ValueError("配布物の ref / sha が不正です。")
        self.revision = (
            ref
            if re.fullmatch(r"[0-9a-f]{40}", ref)
            else session.read(
                f"https://api.github.com/repos/{self.repo}/commits/{quote(ref, safe='')}",
                limit=1024,
                accept="application/vnd.github.sha",
            )
            .decode("ascii")
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("配布物の revision を確定できません。")
        tree = session.json(
            f"https://api.github.com/repos/{self.repo}/git/trees/{self.revision}?recursive=1",
            limit=8 * MAX_FILE_BYTES,
        )
        if tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise ValueError("配布物のファイル一覧が上限を超えています。")
        self.files = {
            item["path"]: item
            for item in tree["tree"]
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        self.hashes: dict[str, str] = {}
        self._text_cache: dict[str, str] = {}

    def read(self, path: str) -> str:
        path = safe_path(path)
        if path in self._text_cache:
            return self._text_cache[path]
        # symlink の親ディレクトリ経由の参照も拒否する。
        parents = [path, *[str(p) for p in PurePosixPath(path).parents if str(p) != "."]]
        if any(self.files.get(p, {}).get("mode") == "120000" for p in parents):
            raise ValueError("symlink を参照する配布物は導入できません。")
        item = self.files.get(path)
        if not item or item.get("type") != "blob" or item.get("mode") not in ("100644", "100755"):
            raise ValueError("配布物に必要なファイルがありません。")
        if item.get("size", 0) > MAX_FILE_BYTES:
            raise ValueError("配布ファイルのサイズが上限を超えています。")
        raw = self.session.read(
            f"https://raw.githubusercontent.com/{self.repo}/{self.revision}/{quote(path, safe='/')}"
        )
        # tree と raw が同じ git blob であることを照合する（#862）。
        blob_hash = hashlib.sha1(
            f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False
        ).hexdigest()
        if blob_hash != item.get("sha"):
            raise ValueError("取得した配布物と確定 revision の hash が一致しません。")
        self.hashes[path] = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
            self._text_cache[path] = text
            return text
        except UnicodeError as exc:
            raise ValueError("配布ファイルが UTF-8 のテキストではありません。") from exc

    def optional_json(self, path: str) -> dict[str, Any]:
        if path not in self.files:
            return {}
        try:
            value = json.loads(self.read(path))
        except json.JSONDecodeError as exc:
            raise ValueError("plugin の定義 JSON が不正です。") from exc
        if not isinstance(value, dict):
            raise ValueError("plugin の定義は object である必要があります。")
        return value


def _author(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name", "")
    return value if isinstance(value, str) else ""


def _public_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return value


def _catalog_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """変換に必要な構造だけ保持し、資格情報・command 引数を応答へコピーしない。"""
    result = {key: raw[key] for key in ("strict", "skills") if key in raw}
    for key in COMPONENTS_NOT_RUN:
        if raw.get(key):
            result[key] = True
    mcp = raw.get("mcpServers")
    if isinstance(mcp, str):
        result["mcpServers"] = mcp
    elif isinstance(mcp, dict):
        result["mcpServers"] = {
            name: {
                "url": _public_url(config.get("url")),
                "type": config.get("type") or "http",
                "command": bool(config.get("command")),
                "headers": bool(config.get("headers")),
            }
            if isinstance(config, dict)
            else None
            for name, config in mcp.items()
        }
    return result


def entry_repository(entry: MarketplaceEntry) -> tuple[str, str, str]:
    source = entry.source
    if isinstance(source, str):
        if not entry.repository or not entry.revision:
            raise ValueError(
                "相対 source の取得元がありません。raw GitHub の JSON URL を使用してください。"
            )
        return entry.repository, entry.revision, safe_path(source, allow_root=True)
    if not isinstance(source, dict):
        raise ValueError("plugin の配布先 source がありません。")
    kind = source.get("source")
    if kind not in ("url", "github", "git-subdir"):
        raise ValueError(
            "この source 形式は未対応です。archive / npm / command / SSH の導入は行いません。"
        )
    repo = github_repository(str(source.get("repo" if kind == "github" else "url", "")))
    root = safe_path(source.get("path", "."), allow_root=True) if kind == "git-subdir" else ""
    sha = source.get("sha")
    if sha is not None and (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)):
        raise ValueError("source.sha は 40 桁の commit SHA を指定してください。")
    return repo, sha or source.get("ref") or "HEAD", root


def fetch_catalog(url: str, marketplace_id: str, timeout: float) -> MarketplaceListing:
    with DownloadSession(url, timeout) as session:
        origin = catalog_origin(url)
        repo = revision = None
        if origin:
            repo, ref, path = origin
            snapshot = GithubSnapshot(session, repo, ref)
            revision = snapshot.revision
            try:
                payload = json.loads(snapshot.read(path))
            except json.JSONDecodeError as exc:
                raise ValueError("配布元の一覧 JSON が不正です。") from exc
        else:
            payload = session.json(url)
        if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
            raise ValueError("plugins の一覧を含むカタログ JSON が必要です。")
        if len(payload["plugins"]) > 1000:
            raise ValueError("カタログの項目数が上限を超えています。")
        plugins: list[PluginManifest | MarketplaceEntry] = []
        used: set[str] = set()
        native_ids = Counter(
            raw["id"]
            for raw in payload["plugins"]
            if isinstance(raw, dict) and "source" not in raw and isinstance(raw.get("id"), str)
        )
        for index, raw in enumerate(payload["plugins"]):
            if isinstance(raw, dict) and "source" not in raw and "id" in raw:
                try:
                    native = PluginManifest.model_validate(raw)
                    if native_ids[native.id] > 1:
                        plugins.append(
                            MarketplaceEntry(
                                id=external_id("duplicate", marketplace_id, str(index), native.id),
                                name=native.name,
                                unavailable_reason="カタログに同じ plugin ID が重複しています。",
                            )
                        )
                    else:
                        used.add(native.id)
                        plugins.append(native)
                    continue
                except ValidationError:
                    pass
            raw = raw if isinstance(raw, dict) else {}
            raw_name = raw.get("name")
            name = raw_name if isinstance(raw_name, str) else f"未対応の項目 {index + 1}"
            pid = external_id("plugin", marketplace_id, name)
            entry = MarketplaceEntry(
                id=pid,
                name=name,
                version=str(raw.get("version") or "0.0.0"),
                description=str(raw.get("description") or ""),
                author=_author(raw.get("author")),
                source=raw.get("source") if isinstance(raw.get("source"), (str, dict)) else None,
                upstream=_catalog_metadata(raw),
                repository=repo,
                revision=revision,
            )
            try:
                entry_repository(entry)
            except ValueError as exc:
                entry.unavailable_reason = str(exc)
            if isinstance(entry.source, dict):
                # 未対応 source の種類は保ち、資格情報・command 引数は一覧へ出さない。
                entry.source = {
                    key: _public_url(value) if key == "url" else value
                    for key, value in entry.source.items()
                    if key in ("source", "url", "repo", "path", "package", "ref", "sha")
                }
            if pid in used:
                for previous in plugins:
                    if previous.id == pid and isinstance(previous, MarketplaceEntry):
                        previous.unavailable_reason = "カタログに同じ plugin 名が重複しています。"
                entry.id = external_id("duplicate", marketplace_id, str(index), name)
                entry.unavailable_reason = (
                    "カタログに同じ plugin 名が重複しています。配布元の定義を確認してください。"
                )
            used.add(entry.id)
            plugins.append(entry)
        return MarketplaceListing(
            name=str(payload.get("name") or ""), plugins=plugins, revision=revision
        )


def _paths(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError("skills はディレクトリの path または path の一覧で指定してください。")


def prepare_import(
    entry: PluginManifest | MarketplaceEntry, marketplace_id: str
) -> PluginImportPreview:
    if isinstance(entry, PluginManifest):
        manifest = entry.model_copy(deep=True)
    else:
        if entry.unavailable_reason:
            raise ValueError(entry.unavailable_reason)
        repo, ref, root = entry_repository(entry)
        with DownloadSession(
            f"https://api.github.com/repos/{repo}", IMPORT_TIMEOUT_SECONDS
        ) as session:
            snapshot = GithubSnapshot(session, repo, ref)
            manifest = _build_manifest(snapshot, entry, marketplace_id, root)
    canonical = manifest.model_dump(mode="json")
    for skill in canonical["skills"]:
        skill.pop("created_at", None)
        skill.pop("updated_at", None)
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return PluginImportPreview(manifest=manifest, digest=digest, warnings=manifest.import_warnings)


def _build_manifest(
    snapshot: GithubSnapshot, entry: MarketplaceEntry, marketplace_id: str, root: str
) -> PluginManifest:
    def at(path: str) -> str:
        path = safe_path(path, allow_root=True)
        return "/".join(p for p in (root, path) if p)

    plugin = snapshot.optional_json(at(".claude-plugin/plugin.json"))
    # strict:false は entry の構成を正本とする。通常は plugin.json の値を優先する。
    component = (
        {**entry.upstream, **plugin}
        if entry.upstream.get("strict") is not False
        else entry.upstream
    )
    warnings = [BASE_WARNING, LICENSE_WARNING]
    for key in COMPONENTS_NOT_RUN:
        if component.get(key) or (
            key in ("commands", "agents", "hooks")
            and any(p.startswith(at(key) + "/") for p in snapshot.files)
        ):
            warnings.append(f"{key} は元製品の実行機能です。この製品では自動実行しません。")
    if any(p.startswith(at("scripts") + "/") for p in snapshot.files):
        warnings.append(
            "配布物の scripts は実行しません。必要な処理は MCP ツールで設定してください。"
        )
    dirs = [] if entry.upstream.get("strict") is False and "skills" in component else [at("skills")]
    if "skills" in component:
        dirs.extend(at(path) for path in _paths(component["skills"]))
    skill_files: list[str] = []
    for directory in dict.fromkeys(dirs):
        # path は Skill 自体、または Skill ディレクトリをまとめたフォルダ。
        direct = "/".join(p for p in (directory, "SKILL.md") if p)
        found = (
            [direct]
            if direct in snapshot.files
            else [
                p
                for p in snapshot.files
                if (not directory or p.startswith(directory + "/")) and p.endswith("/SKILL.md")
            ]
        )
        if directory != at("skills") and not found:
            raise ValueError("明示的な skills path に SKILL.md がありません。")
        skill_files.extend(found)
    skill_files = sorted(set(skill_files))
    if len(skill_files) > MAX_SKILLS:
        raise ValueError("plugin の Skill 数が導入上限を超えています。")
    skills = []
    resources: list[PluginResource] = []
    reference_bytes = 0
    for path in skill_files:
        folder = str(PurePosixPath(path).parent)
        prefix = folder + "/" if folder != "." else ""
        # サービス外での保持を明示的に禁じた資料は、Skill 本文を取り込む前に断る。
        license_paths = [
            p
            for p in (at("LICENSE"), f"{prefix}LICENSE.txt", f"{prefix}LICENSE")
            if p in snapshot.files
        ]
        licenses = {p: snapshot.read(p) for p in dict.fromkeys(license_paths)}
        if any(
            "outside the services" in notice.lower() and "retain copies" in notice.lower()
            for notice in licenses.values()
        ):
            raise ValueError(
                "配布元の利用条件がサービス外での保持を制限するため、この Skill は導入できません。"
            )
        text = snapshot.read(path)
        data, body = _split_frontmatter(text)
        if not body.strip() or not isinstance(data.get("name"), str) or not data.get("description"):
            raise ValueError("SKILL.md に name・description・非空の本文が必要です。")
        sid = external_id("skill", marketplace_id, entry.name, path)
        skill = _skill_from_mapping(data, body, fallback_id=sid, source=f"plugin:{entry.id}")
        if skill is None:
            raise ValueError("SKILL.md を読み取れません。")
        skill.id = sid
        skill.resource_ids = []
        if data.get("allowed-tools") or skill.mcp_requirements:
            warnings.append(
                f"{skill.name}: 元製品のツール指定は使いません。MCP はこの製品で設定してください。"
            )
        skill.mcp_requirements = []
        references = [
            p
            for p in snapshot.files
            if p.startswith(prefix)
            and p not in skill_files
            and p.endswith(".md")
            and "/scripts/" not in p
        ]
        for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", body):
            if target.startswith("#"):
                continue
            if _parse_url(target).scheme:
                warnings.append(f"{skill.name}: 外部リンクの内容は自動取得しません。")
                continue
            try:
                linked = safe_path(f"{prefix}{target.split('#', 1)[0]}")
            except ValueError:
                warnings.append(f"{skill.name}: Skill の範囲外を参照するリンクは導入しません。")
                continue
            if linked != path and linked not in references and linked not in licenses:
                warnings.append(
                    f"{skill.name}: 同梱されていない文書や未対応の形式へのリンクがあります。"
                    "必要な参照内容は別途確認してください。"
                )
        if any(p.startswith(folder + "/scripts/") for p in snapshot.files):
            warnings.append(f"{skill.name}: scripts を必要とする操作はこの製品では実行できません。")
        if any(
            p.startswith(prefix)
            and p not in licenses
            and not p.endswith(".md")
            and item.get("type") == "blob"
            for p, item in snapshot.files.items()
        ):
            warnings.append(
                f"{skill.name}: Markdown 以外の補助ファイルは導入しません。"
                "文書生成や画像・データの操作には対応するツールが必要です。"
            )
        for reference in sorted(set(references) | set(licenses)):
            rid = external_id("reference", marketplace_id, entry.name, reference)
            if any(resource.id == rid for resource in resources):
                skill.resource_ids.append(rid)
                continue
            content = licenses.get(reference)
            if content is None:
                content = snapshot.read(reference)
            reference_bytes += len(content.encode())
            if reference_bytes > MAX_REFERENCE_BYTES:
                raise ValueError("Skill の参照文書が導入上限を超えています。")
            resources.append(
                PluginResource(
                    id=rid,
                    kind="prompt",
                    name=reference,
                    media_type="text/markdown" if reference.endswith(".md") else "text/plain",
                    content=content,
                    metadata={
                        "path": reference,
                        "upstream_revision": snapshot.revision,
                        "external_skill_reference": True,
                    },
                )
            )
            skill.resource_ids.append(rid)
        skills.append(skill)
    servers = []
    mcp = component.get("mcpServers")
    if isinstance(mcp, str):
        mcp = snapshot.optional_json(at(mcp))
    elif mcp is None:
        mcp = snapshot.optional_json(at(".mcp.json"))
    if isinstance(mcp, dict) and "mcpServers" in mcp:
        mcp = mcp["mcpServers"]
    if mcp and not isinstance(mcp, dict):
        raise ValueError("MCP の配布定義が不正です。")
    for name, config in (mcp or {}).items():
        url = config.get("url") if isinstance(config, dict) else None
        parsed = _parse_url(url) if isinstance(url, str) else None
        if (
            parsed is None
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or config.get("command")
            or config.get("headers")
            or "${" in (url or "")
            or parsed.scheme != "https"
            or config.get("type", "http") != "http"
        ):
            warnings.append(
                f"MCP {name}: stdio・秘密情報・動的設定・HTTP 以外の接続は自動導入できません。"
            )
            continue
        servers.append(
            McpConnectionConfig(
                server_id=external_id("mcp", marketplace_id, entry.name, name),
                label=name,
                base_url=url,
                auth_mode="none",
            )
        )
    for skill in skills:
        skill.mcp_requirements = [
            SkillMcpRequirement(server_id=server.server_id) for server in servers
        ]
    if not skills and not servers:
        raise ValueError(
            "対応する Skill / HTTP MCP がありません。commands・agents・hooks は導入できません。"
        )
    return PluginManifest(
        id=entry.id,
        name=entry.name,
        description=str(plugin.get("description") or entry.description),
        version=str(plugin.get("version") or entry.version),
        author=_author(plugin.get("author") or entry.author),
        skills=skills,
        resources=resources,
        mcp_servers=servers,
        import_warnings=list(dict.fromkeys(warnings)),
        import_metadata={
            "format": "github-catalog-v1",
            "marketplace_id": marketplace_id,
            "upstream_name": entry.name,
            "repository": snapshot.repo,
            "revision": snapshot.revision,
            "root": root,
            "files_sha256": snapshot.hashes,
        },
    )
