#!/usr/bin/env python3
"""Build and query a canonical known issues register from audit reports."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import json
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader


SEVERITY_WORDS = {
    "critical",
    "high",
    "medium",
    "low",
    "informational",
    "info",
}

SUPPORTED_REPORT_SUFFIXES = {
    ".pdf",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".json",
}

AUDIT_PATH_HINTS = (
    "audit",
    "audits",
    "report",
    "reports",
    "finding",
    "findings",
    "security",
    "assessment",
    "review",
)


@dataclasses.dataclass
class PreparedSource:
    source_id: str
    input_source: str
    resolved_source: str
    source_type: str
    local_artifact_path: str
    normalized_text_path: str
    extraction_status: str = "pending"
    warnings: list[str] = dataclasses.field(default_factory=list)
    sha1: str = ""


@dataclasses.dataclass
class IssueCandidate:
    title: str
    summary: str
    root_cause: str
    impact: str
    affected_component: str
    severity: str
    source: str
    aliases: list[str]
    source_id: str = ""
    source_location: str = ""
    evidence_snippet: str = ""
    extraction_confidence: str = "medium"


@dataclasses.dataclass
class CanonicalIssue:
    issue_id: str
    title: str
    summary: str
    root_cause: str
    impact: str
    affected_component: str
    severity: str
    aliases: list[str]
    source_reports: list[str]
    canonical_key: str
    source_ids: list[str] = dataclasses.field(default_factory=list)
    evidence: list[dict[str, str]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class CanonicalIssueDraft:
    title: str
    summary: str
    root_cause: str
    impact: str
    affected_component: str
    severity: str
    aliases: list[str]
    source_reports: list[str]
    source_ids: list[str]
    evidence: list[dict[str, str]]


def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value, re.IGNORECASE))


def is_supported_report_path(path_value: str) -> bool:
    return Path(path_value).suffix.lower() in SUPPORTED_REPORT_SUFFIXES


def looks_like_audit_path(path_value: str) -> bool:
    lowered = path_value.lower()
    return any(hint in lowered for hint in AUDIT_PATH_HINTS)


def parse_github_container_url(url: str) -> dict[str, str] | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if len(parts) == 2:
        return {"owner": owner, "repo": repo, "ref": "", "path": ""}
    if len(parts) >= 4 and parts[2] == "tree":
        return {
            "owner": owner,
            "repo": repo,
            "ref": parts[3],
            "path": "/".join(parts[4:]),
        }
    return None


def normalize_remote_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo = parts[0], parts[1]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{parts[3]}/{rest}"
    if parsed.netloc == "raw.githubusercontent.com":
        return url
    if parsed.query == "raw=1":
        return urllib.parse.urlunparse(parsed._replace(query=""))
    return url


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "kit-known-issue-triager/2.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def github_default_branch(owner: str, repo: str) -> str:
    payload = fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
    default_branch = payload.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise RuntimeError(f"could not determine default branch for https://github.com/{owner}/{repo}")
    return default_branch


def expand_github_container(url: str) -> list[str]:
    parsed = parse_github_container_url(url)
    if parsed is None:
        return [url]
    owner = parsed["owner"]
    repo = parsed["repo"]
    ref = parsed["ref"] or github_default_branch(owner, repo)
    base_path = parsed["path"].strip("/")
    tree_payload = fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1")
    tree_items = tree_payload.get("tree", [])
    candidate_paths = [
        item["path"]
        for item in tree_items
        if item.get("type") == "blob"
        and isinstance(item.get("path"), str)
        and is_supported_report_path(item["path"])
        and (not base_path or item["path"].startswith(base_path + "/") or item["path"] == base_path)
    ]
    audit_paths = [path for path in candidate_paths if looks_like_audit_path(path)]
    selected_paths = audit_paths or candidate_paths
    return [f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}" for path in selected_paths]


def expand_local_directory(path: Path) -> list[str]:
    candidate_paths = [
        file_path.resolve().as_posix()
        for file_path in path.rglob("*")
        if file_path.is_file() and is_supported_report_path(file_path.as_posix())
    ]
    audit_paths = [candidate for candidate in candidate_paths if looks_like_audit_path(candidate)]
    return audit_paths or candidate_paths


def expand_raw_sources(raw_sources: list[str]) -> list[str]:
    expanded: list[str] = []
    for raw_source in raw_sources:
        if is_url(raw_source):
            expanded.extend(expand_github_container(raw_source))
            continue
        local_path = Path(raw_source)
        if local_path.exists() and local_path.is_dir():
            expanded.extend(expand_local_directory(local_path))
            continue
        expanded.append(raw_source)
    return dedupe_list(expanded)


def fetch_remote_bytes(source: str) -> tuple[bytes, str, str]:
    normalized = normalize_remote_url(source)
    request = urllib.request.Request(
        normalized,
        headers={
            "User-Agent": "kit-known-issue-triager/2.0",
            "Accept": "text/plain,text/html,application/json,text/markdown,application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            resolved_url = response.geturl()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch {source}: {exc}") from exc
    return payload, resolved_url, content_type


def read_local_bytes(source: str) -> tuple[bytes, str, str]:
    path = Path(source)
    if not path.exists():
        raise RuntimeError(f"input path does not exist: {source}")
    suffix = path.suffix.lower()
    guessed = {
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")
    return path.read_bytes(), str(path.resolve()), guessed


def sha1_hex(payload: bytes) -> str:
    return hashlib.sha1(payload).hexdigest()


def detect_source_type(source: str, resolved_source: str, content_type: str, payload: bytes) -> str:
    lower_source = f"{source} {resolved_source}".lower()
    lower_content_type = content_type.lower()
    if payload.startswith(b"%PDF") or "application/pdf" in lower_content_type or ".pdf" in lower_source:
        return "pdf"
    if "application/json" in lower_content_type or lower_source.endswith(".json"):
        return "json"
    if "text/html" in lower_content_type or "<html" in payload[:2048].decode("utf-8", errors="ignore").lower():
        return "html"
    if lower_source.endswith(".md") or lower_source.endswith(".markdown"):
        return "markdown"
    if lower_source.endswith(".txt") or "text/plain" in lower_content_type:
        return "text"
    return "text"


def write_artifact(workspace_dir: Path, source_id: str, source_type: str, payload: bytes, original_source: str, is_remote: bool) -> Path:
    downloads_dir = workspace_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    suffix = infer_suffix(source_type, original_source)
    if not is_remote:
        return Path(original_source).resolve()
    artifact_path = downloads_dir / f"{source_id}{suffix}"
    artifact_path.write_bytes(payload)
    return artifact_path


def infer_suffix(source_type: str, source: str) -> str:
    parsed = urllib.parse.urlparse(source)
    suffix = Path(parsed.path).suffix if parsed.scheme else Path(source).suffix
    if suffix:
        return suffix
    return {
        "pdf": ".pdf",
        "json": ".json",
        "html": ".html",
        "markdown": ".md",
        "text": ".txt",
    }.get(source_type, ".bin")


def clean_text(content: str, source_type: str) -> str:
    stripped = content.strip()
    if source_type == "json" or stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        return "\n".join(iter_json_strings(parsed))

    if source_type == "html" or "<html" in content.lower() or "</p>" in content.lower() or "</div>" in content.lower():
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", content)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p>", "\n\n", text)
        text = re.sub(r"(?i)</div>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return html.unescape(re.sub(r"[ \t]+\n", "\n", text))

    return content


def iter_json_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, dict):
        preferred_keys = [
            "title",
            "name",
            "severity",
            "description",
            "summary",
            "impact",
            "root_cause",
            "recommendation",
        ]
        for key in preferred_keys:
            if key in value and isinstance(value[key], str):
                strings.append(f"{key}: {value[key]}")
        for child in value.values():
            strings.extend(iter_json_strings(child))
        return strings
    if isinstance(value, list):
        for item in value:
            strings.extend(iter_json_strings(item))
        return strings
    if isinstance(value, str):
        return [value]
    return strings


def extract_pdf_text(path: Path) -> str:
    parts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = collapse_ws(page.extract_text() or "")
                if text:
                    parts.append(f"--- Page {page_index} ---\n{text}")
    except Exception:
        parts = []
    if parts:
        return "\n\n".join(parts)

    reader = PdfReader(str(path))
    fallback_parts: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = collapse_ws(page.extract_text() or "")
        if text:
            fallback_parts.append(f"--- Page {page_index} ---\n{text}")
    return "\n\n".join(fallback_parts)


def normalize_source_text(payload: bytes, source_type: str, artifact_path: Path) -> str:
    if source_type == "pdf":
        return extract_pdf_text(artifact_path)
    content = payload.decode("utf-8", errors="replace")
    return clean_text(content, source_type)


def prepare_sources(raw_sources: list[str], workspace_dir: Path) -> tuple[list[PreparedSource], list[str], list[str]]:
    sources_dir = workspace_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    expanded_sources = expand_raw_sources(raw_sources)
    prepared_sources: list[PreparedSource] = []
    for index, raw_source in enumerate(expanded_sources, start=1):
        source_id = f"SRC-{index:03d}"
        is_remote_source = is_url(raw_source)
        payload, resolved_source, content_type = (
            fetch_remote_bytes(raw_source) if is_remote_source else read_local_bytes(raw_source)
        )
        source_type = detect_source_type(raw_source, resolved_source, content_type, payload)
        artifact_path = write_artifact(workspace_dir, source_id, source_type, payload, raw_source, is_remote_source)
        normalized_text = normalize_source_text(payload, source_type, artifact_path)
        normalized_path = sources_dir / f"{source_id}.txt"
        normalized_path.write_text(normalized_text, encoding="utf-8")

        warnings: list[str] = []
        if not normalized_text.strip():
            warnings.append("No normalized text extracted from source.")
        elif len(normalized_text.strip()) < 200:
            warnings.append("Normalized text is very short; extraction quality may be weak.")

        prepared_sources.append(
            PreparedSource(
                source_id=source_id,
                input_source=raw_source,
                resolved_source=resolved_source,
                source_type=source_type,
                local_artifact_path=str(artifact_path),
                normalized_text_path=str(normalized_path),
                warnings=warnings,
                sha1=sha1_hex(payload),
            )
        )

    return prepared_sources, raw_sources, expanded_sources


def normalize_severity(value: str) -> str:
    normalized = collapse_ws(value).lower()
    if normalized == "info":
        return "informational"
    if normalized in SEVERITY_WORDS:
        return normalized
    return "unspecified"


def canonical_key_for(candidate: IssueCandidate) -> str:
    component = slugify(candidate.affected_component)
    cause = slugify(candidate.root_cause or candidate.title)
    digest = hashlib.sha1(f"{component}:{cause}".encode("utf-8")).hexdigest()[:10]
    prefix = "-".join(part for part in [component[:20], cause[:28]] if part).strip("-")
    prefix = prefix or slugify(candidate.title)[:32] or "issue"
    return f"{prefix}-{digest}"


def canonical_key_for_issue(issue: CanonicalIssueDraft | CanonicalIssue) -> str:
    candidate = IssueCandidate(
        title=issue.title,
        summary=issue.summary,
        root_cause=issue.root_cause,
        impact=issue.impact,
        affected_component=issue.affected_component,
        severity=issue.severity,
        source=issue.source_reports[0] if issue.source_reports else "known issue",
        aliases=issue.aliases or [issue.title],
        source_id=issue.source_ids[0] if issue.source_ids else "",
    )
    return canonical_key_for(candidate)


def canonical_issue_from_draft(index: int, draft: CanonicalIssueDraft) -> CanonicalIssue:
    return CanonicalIssue(
        issue_id=f"KI-{index:03d}",
        title=draft.title,
        summary=draft.summary,
        root_cause=draft.root_cause,
        impact=draft.impact,
        affected_component=draft.affected_component,
        severity=normalize_severity(draft.severity),
        aliases=dedupe_list(draft.aliases or [draft.title]),
        source_reports=dedupe_list(draft.source_reports),
        canonical_key=canonical_key_for_issue(draft),
        source_ids=dedupe_list(draft.source_ids),
        evidence=dedupe_evidence(draft.evidence),
    )


def issue_evidence(candidate: IssueCandidate) -> dict[str, str]:
    return {
        "source": candidate.source,
        "source_id": candidate.source_id,
        "location": candidate.source_location,
        "snippet": candidate.evidence_snippet,
        "original_title": candidate.title,
        "confidence": candidate.extraction_confidence,
    }


def dedupe_evidence(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, str]] = []
    for item in items:
        key = (item.get("source", ""), item.get("location", ""), item.get("original_title", ""), item.get("snippet", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def severity_rank(value: str) -> int:
    order = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "informational": 4,
        "unspecified": 5,
    }
    return order.get(value, 5)


def dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def write_outputs(output_path: Path, issues: list[CanonicalIssue], inputs: list[str], sources: list[PreparedSource]) -> Path:
    payload = {
        "inputs": inputs,
        "sources": [dataclasses.asdict(source) for source in sources],
        "issues": [dataclasses.asdict(issue) for issue in issues],
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def load_known_issues(known_path: Path) -> list[CanonicalIssue]:
    payload = json.loads(known_path.read_text(encoding="utf-8"))
    return [canonical_issue_from_dict(issue) for issue in payload.get("issues", [])]


def load_known_payload(known_path: Path) -> tuple[list[CanonicalIssue], list[PreparedSource]]:
    payload = json.loads(known_path.read_text(encoding="utf-8"))
    issues = [canonical_issue_from_dict(issue) for issue in payload.get("issues", [])]
    sources = [PreparedSource(**source) for source in payload.get("sources", [])]
    return issues, sources


def canonical_issue_from_dict(data: dict[str, Any]) -> CanonicalIssue:
    return CanonicalIssue(
        issue_id=data.get("issue_id", ""),
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        root_cause=data.get("root_cause", ""),
        impact=data.get("impact", ""),
        affected_component=data.get("affected_component", "Unspecified component"),
        severity=data.get("severity", "unspecified"),
        aliases=data.get("aliases", []),
        source_reports=data.get("source_reports", []),
        canonical_key=data.get("canonical_key", slugify(data.get("title", "issue"))),
        source_ids=data.get("source_ids", []),
        evidence=data.get("evidence", []),
    )


def normalize_evidence_item(raw: dict[str, Any], source_ids: list[str], source_reports: list[str]) -> dict[str, str]:
    return {
        "source": collapse_ws(str(raw.get("source", ""))) or (source_reports[0] if source_reports else ""),
        "source_id": collapse_ws(str(raw.get("source_id", ""))) or (source_ids[0] if source_ids else ""),
        "location": collapse_ws(str(raw.get("location", ""))),
        "snippet": collapse_ws(str(raw.get("snippet", "")))[:300],
        "original_title": collapse_ws(str(raw.get("original_title", ""))),
        "confidence": collapse_ws(str(raw.get("confidence", ""))).lower() or "medium",
    }


def normalize_llm_canonical_issue(raw_issue: dict[str, Any]) -> CanonicalIssueDraft:
    title = collapse_ws(str(raw_issue.get("title", ""))) or "Untitled issue"
    summary = collapse_ws(str(raw_issue.get("summary", ""))) or title
    root_cause = collapse_ws(str(raw_issue.get("root_cause", ""))) or title
    impact = collapse_ws(str(raw_issue.get("impact", ""))) or "Impact not explicitly stated in source report."
    component = collapse_ws(str(raw_issue.get("affected_component", ""))) or "Unspecified component"
    severity = normalize_severity(str(raw_issue.get("severity", "")))

    aliases = raw_issue.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = [str(aliases)]
    source_reports = raw_issue.get("source_reports", [])
    if not isinstance(source_reports, list):
        source_reports = [str(source_reports)]
    source_ids = raw_issue.get("source_ids", [])
    if not isinstance(source_ids, list):
        source_ids = [str(source_ids)]
    evidence = raw_issue.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []

    return CanonicalIssueDraft(
        title=title,
        summary=summary,
        root_cause=root_cause,
        impact=impact,
        affected_component=component,
        severity=severity,
        aliases=dedupe_list([title] + [collapse_ws(str(alias)) for alias in aliases if collapse_ws(str(alias))]),
        source_reports=dedupe_list([collapse_ws(str(source)) for source in source_reports if collapse_ws(str(source))]),
        source_ids=dedupe_list([collapse_ws(str(source_id)) for source_id in source_ids if collapse_ws(str(source_id))]),
        evidence=[normalize_evidence_item(item, source_ids, source_reports) for item in evidence if isinstance(item, dict)],
    )


def apply_llm_deduplication_from_payload(payload: dict[str, Any]) -> list[CanonicalIssue] | None:
    raw_issues = payload.get("canonical_issues")
    if not isinstance(raw_issues, list):
        return None
    normalized = [normalize_llm_canonical_issue(item) for item in raw_issues if isinstance(item, dict)]
    normalized.sort(key=lambda issue: (severity_rank(issue.severity), issue.title.lower()))
    return [canonical_issue_from_draft(index, issue) for index, issue in enumerate(normalized, start=1)]


def require_llm_canonical_issues(payload: dict[str, Any]) -> list[CanonicalIssue]:
    raw_issues = payload.get("canonical_issues")
    if not isinstance(raw_issues, list):
        raise RuntimeError(
            "missing canonical_issues in staged JSON; LLM-authored dedupe is required"
        )
    issues = apply_llm_deduplication_from_payload(payload) or []
    extracted_issue_count = sum(
        len(result.get("issues", []))
        for result in payload.get("source_results", [])
        if isinstance(result, dict)
    )
    existing_issue_count = len(payload.get("existing_issues_snapshot", []))
    if not issues and (extracted_issue_count > 0 or existing_issue_count > 0):
        raise RuntimeError(
            "empty canonical_issues in staged JSON; LLM-authored dedupe is required"
        )
    return issues


def canonical_issue_to_candidate(issue: CanonicalIssue) -> IssueCandidate:
    evidence = issue.evidence[0] if issue.evidence else {}
    return IssueCandidate(
        title=issue.title,
        summary=issue.summary,
        root_cause=issue.root_cause,
        impact=issue.impact,
        affected_component=issue.affected_component,
        severity=issue.severity,
        source=issue.source_reports[0] if issue.source_reports else "existing known issue",
        aliases=issue.aliases or [issue.title],
        source_id=issue.source_ids[0] if issue.source_ids else "EXISTING",
        source_location=evidence.get("location", ""),
        evidence_snippet=evidence.get("snippet", ""),
        extraction_confidence=evidence.get("confidence", "high"),
    )


def merge_source_lists(existing_sources: list[PreparedSource], new_sources: list[PreparedSource]) -> list[PreparedSource]:
    merged: list[PreparedSource] = []
    seen: set[tuple[str, str]] = set()
    for source in existing_sources + new_sources:
        key = (source.source_id, source.input_source)
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def create_workspace(workspace_dir: str | None) -> Path:
    if workspace_dir:
        path = Path(workspace_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="known-issues-"))


def load_state_payload(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def write_state_payload(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_prepared_sources_from_payload(payload: dict[str, Any]) -> tuple[list[PreparedSource], dict[str, PreparedSource]]:
    sources = [PreparedSource(**item) for item in payload.get("sources", [])]
    return sources, {source.source_id: source for source in sources}


def duplicate_check_contract() -> dict[str, Any]:
    return {
        "finding_extraction": {
            "task": "Read report_text and extract each distinct security issue as a separate finding.",
            "rules": [
                "Do not merge multiple findings into one.",
                "Do not invent findings that are not supported by report_text.",
                "Keep wording close to the source when summarizing.",
                "If the input is already a single issue, return exactly one finding.",
            ],
            "required_fields": [
                "title",
                "summary",
                "root_cause",
                "impact",
                "affected_component",
                "severity",
                "evidence_snippet",
            ],
        },
        "parallelization": {
            "task": "After extracting findings, evaluate them independently.",
            "rules": [
                "If more than one finding is identified and delegation is available, spawn exactly one worker per finding.",
                "Each worker receives one finding, the full known_issues list, and the duplicate_check contract.",
                "Do not batch multiple findings into one worker.",
                "After all workers finish, merge their outputs into one final ordered result list.",
            ],
        },
        "duplicate_check": {
            "task": "For one finding at a time, decide whether it is already covered by the known_issues register.",
            "verdicts": ["known", "possibly-known", "new"],
            "decision_rules": [
                "Return known only when the underlying root cause, affected surface, and impact are materially the same as an existing known issue.",
                "Return possibly-known when there is a plausible match but the evidence is not strong enough for known.",
                "Return new when the finding differs in bug class, exploit path, preconditions, or impact in a way that makes it a separate issue.",
                "Do not match on component name alone.",
                "Do not match on severity alone.",
                "Wording differences do not matter if the underlying issue is the same.",
                "Explain why the closest match is or is not the same issue.",
            ],
            "comparison_dimensions": [
                "root_cause",
                "affected_component",
                "exploit_path_or_preconditions",
                "impact",
                "severity_context",
            ],
            "required_output_fields": [
                "finding_index",
                "finding_title",
                "verdict",
                "confidence",
                "matched_issue_id",
                "matched_issue_title",
                "rationale",
            ],
            "output_schema": {
                "finding_index": "1-based integer index of the finding in the extracted finding list",
                "finding_title": "short title for the finding being evaluated",
                "verdict": "known | possibly-known | new",
                "confidence": "high | medium | low",
                "matched_issue_id": "string or empty string",
                "matched_issue_title": "string or empty string",
                "rationale": "short explanation grounded in the comparison dimensions",
            },
        },
    }


def normalize_claude_issue(raw_issue: dict[str, Any], source: PreparedSource) -> IssueCandidate:
    title = collapse_ws(str(raw_issue.get("title", ""))) or "Untitled issue"
    summary = collapse_ws(str(raw_issue.get("summary", ""))) or title
    root_cause = collapse_ws(str(raw_issue.get("root_cause", ""))) or title
    impact = collapse_ws(str(raw_issue.get("impact", ""))) or "Impact not explicitly stated in source report."
    component = collapse_ws(str(raw_issue.get("affected_component", ""))) or "Unspecified component"
    severity = normalize_severity(str(raw_issue.get("severity", "")))
    aliases = raw_issue.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = [str(aliases)]
    location = collapse_ws(str(raw_issue.get("source_location", "")))
    snippet = collapse_ws(str(raw_issue.get("evidence_snippet", "")))[:300]
    confidence = collapse_ws(str(raw_issue.get("extraction_confidence", ""))).lower() or "medium"
    return IssueCandidate(
        title=title,
        summary=summary,
        root_cause=root_cause,
        impact=impact,
        affected_component=component,
        severity=severity,
        source=source.input_source,
        aliases=dedupe_list([title] + [str(alias) for alias in aliases]),
        source_id=source.source_id,
        source_location=location,
        evidence_snippet=snippet,
        extraction_confidence=confidence,
    )


def apply_claude_extractions(prepared_sources: list[PreparedSource], extractions_path: Path) -> list[IssueCandidate]:
    payload = json.loads(extractions_path.read_text(encoding="utf-8"))
    source_lookup = {source.source_id: source for source in prepared_sources}
    results = payload.get("source_results", payload if isinstance(payload, list) else [])
    candidates: list[IssueCandidate] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = result.get("source_id", "")
        if source_id not in source_lookup:
            continue
        source = source_lookup[source_id]
        seen_sources.add(source_id)
        source.extraction_status = result.get("status", "ok")
        extra_warnings = result.get("warnings", [])
        if isinstance(extra_warnings, list):
            source.warnings = dedupe_list(source.warnings + [str(item) for item in extra_warnings])
        for raw_issue in result.get("issues", []):
            candidates.append(normalize_claude_issue(raw_issue, source))

    for source in prepared_sources:
        if source.source_id not in seen_sources:
            source.extraction_status = "failed"
            source.warnings = dedupe_list(source.warnings + ["No Claude extraction result was provided for this source."])
    return candidates


def apply_claude_extractions_from_payload(prepared_sources: list[PreparedSource], payload: dict[str, Any]) -> list[IssueCandidate]:
    source_lookup = {source.source_id: source for source in prepared_sources}
    results = payload.get("source_results", payload if isinstance(payload, list) else [])
    candidates: list[IssueCandidate] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = result.get("source_id", "")
        if source_id not in source_lookup:
            continue
        source = source_lookup[source_id]
        seen_sources.add(source_id)
        source.extraction_status = result.get("status", "ok")
        extra_warnings = result.get("warnings", [])
        if isinstance(extra_warnings, list):
            source.warnings = dedupe_list(source.warnings + [str(item) for item in extra_warnings])
        for raw_issue in result.get("issues", []):
            candidates.append(normalize_claude_issue(raw_issue, source))

    for source in prepared_sources:
        if source.source_id not in seen_sources:
            source.extraction_status = "failed"
            source.warnings = dedupe_list(source.warnings + ["No extraction result was provided for this source."])
    return candidates


def run_prepare_build(args: argparse.Namespace) -> int:
    workspace_dir = create_workspace(args.workspace_dir)
    prepared_sources, requested_inputs, expanded_inputs = prepare_sources(args.input, workspace_dir)
    state_path = Path(args.state_file)
    existing_payload = load_state_payload(state_path)
    merge_known_path = Path(args.merge_known) if args.merge_known else None
    payload = {
        "status": "prepared",
        "workspace_dir": str(workspace_dir),
        "requested_inputs": requested_inputs,
        "expanded_inputs": expanded_inputs,
        "sources": [dataclasses.asdict(source) for source in prepared_sources],
        "source_results": [],
        "canonical_issues": [],
    }
    if merge_known_path:
        existing_issues, existing_sources = load_known_payload(merge_known_path)
        payload["existing_issues_snapshot"] = [dataclasses.asdict(issue) for issue in existing_issues]
        payload["existing_sources_snapshot"] = [dataclasses.asdict(source) for source in existing_sources]
    elif existing_payload.get("issues"):
        payload["existing_issues_snapshot"] = existing_payload.get("issues", [])
        payload["existing_sources_snapshot"] = existing_payload.get("sources", [])
    write_state_payload(state_path, payload)
    print(
        json.dumps(
            {
                "status": "ok",
                "workspace_dir": str(workspace_dir),
                "state_file": str(state_path),
                "sources": [dataclasses.asdict(source) for source in prepared_sources],
                "existing_issue_count": len(payload.get("existing_issues_snapshot", [])),
            },
            indent=2,
        )
    )
    return 0


def run_finalize_build(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    state_payload = load_state_payload(state_path)
    prepared_sources, _lookup = load_prepared_sources_from_payload(state_payload)
    candidates = apply_claude_extractions_from_payload(prepared_sources, state_payload)
    existing_sources: list[PreparedSource] = []
    if args.merge_known:
        merge_known_path = Path(args.merge_known)
        if state_payload.get("existing_issues_snapshot") and state_path.resolve() == merge_known_path.resolve():
            existing_issues = [canonical_issue_from_dict(issue) for issue in state_payload.get("existing_issues_snapshot", [])]
            existing_sources = [PreparedSource(**source) for source in state_payload.get("existing_sources_snapshot", [])]
        else:
            existing_issues, existing_sources = load_known_payload(merge_known_path)
        candidates = [canonical_issue_to_candidate(issue) for issue in existing_issues] + candidates
    issues = require_llm_canonical_issues(state_payload)
    all_sources = merge_source_lists(existing_sources, prepared_sources)
    inputs = dedupe_list([source.input_source for source in all_sources])
    output_path = Path(args.output)
    json_path = write_outputs(output_path, issues, inputs, all_sources)
    print(
        json.dumps(
            {
                "status": "ok",
                "sources": inputs,
                "candidate_count": len(candidates),
                "canonical_issue_count": len(issues),
                "output": str(json_path),
            },
            indent=2,
        )
    )
    return 0


def run_prepare_check(args: argparse.Namespace) -> int:
    known_path = Path(args.known)
    known_issues = load_known_issues(known_path)
    if args.issue_file:
        issue_text = Path(args.issue_file).read_text(encoding="utf-8")
        label = args.issue_file
    else:
        issue_text = args.issue_text
        label = "inline issue"
    payload = {
        "status": "prepared",
        "known_path": str(known_path),
        "input": label,
        "known_issues": [dataclasses.asdict(issue) for issue in known_issues],
        "report_text": issue_text,
        "llm_contract": duplicate_check_contract(),
    }
    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output_path),
                "known_issue_count": len(known_issues),
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_build = subparsers.add_parser("prepare-build", help="download and normalize sources for Claude-assisted extraction")
    prepare_build.add_argument("--input", action="append", required=True, help="local path or URL to an audit report")
    prepare_build.add_argument("--state-file", default="known-issues.json", help="single JSON state file used throughout the staged workflow")
    prepare_build.add_argument("--merge-known", help="existing known-issues.json to snapshot into the staged state for extend mode")
    prepare_build.add_argument("--workspace-dir", help="directory where normalized sources should be written")
    prepare_build.set_defaults(func=run_prepare_build)

    finalize_build = subparsers.add_parser("finalize-build", help="merge Claude extraction results and write known-issues.json")
    finalize_build.add_argument("--state-file", default="known-issues.json", help="single JSON state file created by prepare-build and updated with source_results")
    finalize_build.add_argument("--output", default="known-issues.json", help="output JSON path")
    finalize_build.add_argument("--merge-known", help="existing known-issues.json to extend instead of rebuilding from scratch")
    finalize_build.set_defaults(func=run_finalize_build)

    prepare_check = subparsers.add_parser("prepare-check", help="prepare one or more findings for model-assisted duplicate checking")
    prepare_check.add_argument("--known", required=True, help="path to known-issues.json")
    prepare_check.add_argument("--output", default="known-issues-check.json", help="output JSON path for staged duplicate checking")
    prepare_group = prepare_check.add_mutually_exclusive_group(required=True)
    prepare_group.add_argument("--issue-file", help="path to a file containing one or more findings")
    prepare_group.add_argument("--issue-text", help="inline issue text")
    prepare_check.set_defaults(func=run_prepare_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
