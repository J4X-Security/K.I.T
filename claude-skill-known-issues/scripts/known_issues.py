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

TITLE_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "in",
    "on",
    "with",
    "via",
    "from",
    "by",
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
            "User-Agent": "known-issues-aggregator/2.0",
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
            "User-Agent": "known-issues-aggregator/2.0",
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


def split_sections(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []
    heading_re = re.compile(r"^\s{0,3}(#{1,6}\s+.+|\d+\.\s+.+|(?:finding|issue)\s*[:#-]?\s+.+)$", re.IGNORECASE)

    for line in lines:
        if heading_re.match(line.strip()):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip() for section in sections if "\n".join(section).strip()]


def extract_candidates_from_text(text: str, source: PreparedSource) -> list[IssueCandidate]:
    sections = split_sections(text)
    candidates = [candidate for candidate in (section_to_candidate(section, source) for section in sections) if candidate]
    if candidates:
        return candidates
    return fallback_candidates(text, source)


def section_to_candidate(section: str, source: PreparedSource) -> IssueCandidate | None:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if not lines:
        return None

    title = normalize_title(lines[0])
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    summary = extract_labeled(body, ("summary", "description", "issue", "finding")) or first_paragraph(body)
    root_cause = extract_labeled(body, ("root cause", "cause", "vulnerability", "bug")) or infer_root_cause(body, title)
    impact = extract_labeled(body, ("impact", "risk", "consequence")) or infer_impact(body)
    component = extract_labeled(body, ("affected component", "component", "module", "contract", "function")) or infer_component(section)
    severity = infer_severity(section)
    if not looks_like_issue(title, summary, root_cause, impact, component, severity):
        return None

    return IssueCandidate(
        title=title,
        summary=summary or title,
        root_cause=root_cause or summary or title,
        impact=impact or "Impact not explicitly stated in source report.",
        affected_component=component or "Unspecified component",
        severity=severity,
        source=source.input_source,
        aliases=[title],
        source_id=source.source_id,
        source_location=title,
        evidence_snippet=(first_paragraph(body) or title)[:300],
        extraction_confidence="low",
    )


def fallback_candidates(text: str, source: PreparedSource) -> list[IssueCandidate]:
    candidates: list[IssueCandidate] = []
    bullet_re = re.compile(r"^\s*[-*]\s*(?:\[(?P<sev>[^\]]+)\]\s*)?(?P<title>[^:]+):\s*(?P<body>.+)$")
    for line in text.splitlines():
        match = bullet_re.match(line)
        if not match:
            continue
        severity = normalize_severity(match.group("sev") or "")
        title = normalize_title(match.group("title"))
        body = match.group("body").strip()
        if len(title) < 6 or len(body) < 15:
            continue
        candidates.append(
            IssueCandidate(
                title=title,
                summary=body,
                root_cause=infer_root_cause(body, title),
                impact=infer_impact(body),
                affected_component=infer_component(f"{title}\n{body}"),
                severity=severity,
                source=source.input_source,
                aliases=[title],
                source_id=source.source_id,
                source_location=title,
                evidence_snippet=body[:300],
                extraction_confidence="low",
            )
        )
    return candidates


def normalize_title(raw: str) -> str:
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", raw).strip()
    text = re.sub(r"^\d+\.\s*", "", text)
    text = re.sub(r"^(finding|issue)\s*[:#-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text[:160].strip(" -:")


def extract_labeled(body: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*[:\-]\s*(.+)$")
        match = pattern.search(body)
        if match:
            return collapse_ws(match.group(1))
    return ""


def first_paragraph(body: str) -> str:
    paragraphs = [collapse_ws(part) for part in re.split(r"\n\s*\n", body) if collapse_ws(part)]
    return paragraphs[0] if paragraphs else ""


def infer_root_cause(body: str, title: str) -> str:
    text = collapse_ws(body)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in ("because", "due to", "allows", "fails to", "does not", "missing", "unchecked", "unsaf")):
            return sentence
    return title


def infer_impact(body: str) -> str:
    text = collapse_ws(body)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in ("can ", "could ", "may ", "result", "lead to", "caus", "loss", "drain", "steal", "deny", "block")):
            return sentence
    return "Impact not explicitly stated in source report."


def infer_component(text: str) -> str:
    action_in_component = re.search(r"(?i)\b([a-z][a-z0-9_]*(?:\s+[a-z][a-z0-9_]*){0,2})\s+in\s+([A-Z][A-Za-z0-9_]+)\b", text)
    if action_in_component:
        action = camelize(action_in_component.group(1))
        component = action_in_component.group(2)
        return f"{component}.{action}"
    patterns = [
        r"(?i)\b(contract|module|component|service|function|method)\s+`?([A-Za-z0-9_./:-]+)`?",
        r"(?i)\b(?:in|within|inside)\s+`?([A-Za-z0-9_./:-]+)`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(match.lastindex or 1)
            return candidate.strip("`")
    return "Unspecified component"


def camelize(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    if not parts:
        return ""
    return parts[0].lower() + "".join(part[:1].upper() + part[1:].lower() for part in parts[1:])


def infer_severity(text: str) -> str:
    lower = text.lower()
    for word in ("critical", "high", "medium", "low", "informational", "info"):
        if re.search(rf"\b{word}\b", lower):
            return normalize_severity(word)
    return "unspecified"


def normalize_severity(value: str) -> str:
    normalized = collapse_ws(value).lower()
    if normalized == "info":
        return "informational"
    if normalized in SEVERITY_WORDS:
        return normalized
    return "unspecified"


def looks_like_issue(title: str, summary: str, root_cause: str, impact: str, component: str, severity: str) -> bool:
    text = " ".join([title, summary, root_cause, impact, component, severity]).lower()
    indicators = [
        "attack",
        "vulnerability",
        "bug",
        "overflow",
        "underflow",
        "reentr",
        "dos",
        "denial",
        "loss",
        "steal",
        "drain",
        "bypass",
        "incorrect",
        "missing",
        "unchecked",
        "fails",
    ]
    if any(indicator in text for indicator in indicators):
        return True
    return len(title) > 8 and (severity != "unspecified" or len(summary) > 30)


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


def aggregate_candidates(candidates: list[IssueCandidate]) -> list[CanonicalIssue]:
    canonicals: list[CanonicalIssue] = []
    for candidate in candidates:
        match_index, score = best_match(candidate, canonicals)
        if match_index is None or score < 0.68:
            canonicals.append(
                CanonicalIssue(
                    issue_id=f"KI-{len(canonicals) + 1:03d}",
                    title=candidate.title,
                    summary=candidate.summary,
                    root_cause=candidate.root_cause,
                    impact=candidate.impact,
                    affected_component=candidate.affected_component,
                    severity=candidate.severity,
                    aliases=dedupe_list(candidate.aliases),
                    source_reports=[candidate.source],
                    canonical_key=canonical_key_for(candidate),
                    source_ids=[candidate.source_id] if candidate.source_id else [],
                    evidence=[issue_evidence(candidate)],
                )
            )
            continue

        existing = canonicals[match_index]
        existing.title = prefer_longer(existing.title, candidate.title)
        existing.summary = prefer_longer(existing.summary, candidate.summary)
        existing.root_cause = prefer_longer(existing.root_cause, candidate.root_cause)
        existing.impact = prefer_longer(existing.impact, candidate.impact)
        existing.affected_component = choose_component(existing.affected_component, candidate.affected_component)
        existing.severity = choose_severity(existing.severity, candidate.severity)
        existing.aliases = dedupe_list(existing.aliases + candidate.aliases + [candidate.title])
        existing.source_reports = dedupe_list(existing.source_reports + [candidate.source])
        existing.source_ids = dedupe_list(existing.source_ids + ([candidate.source_id] if candidate.source_id else []))
        existing.evidence = dedupe_evidence(existing.evidence + [issue_evidence(candidate)])
        existing.canonical_key = canonical_key_for(
            IssueCandidate(
                title=existing.title,
                summary=existing.summary,
                root_cause=existing.root_cause,
                impact=existing.impact,
                affected_component=existing.affected_component,
                severity=existing.severity,
                source=existing.source_reports[0],
                aliases=existing.aliases,
                source_id=existing.source_ids[0] if existing.source_ids else "",
            )
        )

    canonicals.sort(key=lambda issue: (severity_rank(issue.severity), issue.title.lower()))
    for index, issue in enumerate(canonicals, start=1):
        issue.issue_id = f"KI-{index:03d}"
    return canonicals


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


def best_match(candidate: IssueCandidate, canonicals: list[CanonicalIssue]) -> tuple[int | None, float]:
    best_index: int | None = None
    best_score = 0.0
    for index, canonical in enumerate(canonicals):
        score = similarity_scores(
            candidate.title,
            canonical.title,
            candidate.root_cause,
            canonical.root_cause,
            candidate.affected_component,
            canonical.affected_component,
            candidate.impact,
            canonical.impact,
        )
        if score > best_score:
            best_index = index
            best_score = score
    return best_index, best_score


def similarity_scores(
    title_a: str,
    title_b: str,
    cause_a: str,
    cause_b: str,
    component_a: str,
    component_b: str,
    impact_a: str,
    impact_b: str,
) -> float:
    title_score = blended_similarity(title_a, title_b)
    cause_score = blended_similarity(cause_a, cause_b)
    component_score = blended_similarity(component_a, component_b)
    impact_score = blended_similarity(impact_a, impact_b)
    bonus = 0.0
    if component_score >= 0.9 and (cause_score >= 0.45 or title_score >= 0.5):
        bonus += 0.12
    if component_score >= 0.75 and cause_score >= 0.55:
        bonus += 0.08
    return min(1.0, (0.3 * title_score) + (0.35 * cause_score) + (0.25 * component_score) + (0.1 * impact_score) + bonus)


def blended_similarity(left: str, right: str) -> float:
    left_norm = canonicalize_similarity_text(left)
    right_norm = canonicalize_similarity_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    seq = __import__("difflib").SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = meaningful_tokens(left_norm)
    right_tokens = meaningful_tokens(right_norm)
    if not left_tokens or not right_tokens:
        return seq
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(seq, overlap)


def meaningful_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", canonicalize_similarity_text(value)) if token not in TITLE_STOPWORDS and len(token) > 2}


def canonicalize_similarity_text(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    text = collapse_ws(text).lower()
    replacements = {
        "does not": "missingcheck",
        "do not": "missingcheck",
        "fails to": "missingcheck",
        "failed to": "missingcheck",
        "failure to": "missingcheck",
        "without": "missingcheck",
        "unchecked": "missingcheck",
        "unvalidated": "missingcheck",
        "omits": "missingcheck",
        "omitted": "missingcheck",
        "skips": "missingcheck",
        "skipped": "missingcheck",
        "bypass": "missingcheck",
        "bypasses": "missingcheck",
        "validation": "check",
        "validate": "check",
        "verifies": "check",
        "verify": "check",
        "return value": "transfer_result",
        "transfer result": "transfer_result",
        "transfer success": "transfer_result",
        "paid without receiving tokens": "accounting_desync",
        "no tokens are delivered": "accounting_desync",
        "reward accounting": "accounting_desync",
        "accounting updated": "accounting_desync",
        "supply cap": "cap",
        "configured cap": "cap",
        "emergency mint": "emergencymint",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def prefer_longer(left: str, right: str) -> str:
    return left if len(left) >= len(right) else right


def choose_component(left: str, right: str) -> str:
    if left == "Unspecified component":
        return right
    if right == "Unspecified component":
        return left
    return prefer_longer(left, right)


def choose_severity(left: str, right: str) -> str:
    return left if severity_rank(left) <= severity_rank(right) else right


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


def check_issue(known_issues: list[CanonicalIssue], candidate: IssueCandidate) -> dict[str, Any]:
    best: CanonicalIssue | None = None
    best_score = 0.0
    for issue in known_issues:
        score = similarity_scores(
            candidate.title,
            issue.title,
            candidate.root_cause,
            issue.root_cause,
            candidate.affected_component,
            issue.affected_component,
            candidate.impact,
            issue.impact,
        )
        if score > best_score:
            best = issue
            best_score = score

    if best is None:
        return {
            "verdict": "new",
            "confidence": "low",
            "score": 0.0,
            "rationale": "No known issues were available for comparison.",
        }

    if best_score >= 0.66:
        verdict = "known"
        confidence = "high"
    elif best_score >= 0.5:
        verdict = "possibly-known"
        confidence = "medium"
    else:
        verdict = "new"
        confidence = "medium"

    overlap = sorted(meaningful_tokens(candidate.root_cause + " " + candidate.title) & meaningful_tokens(best.root_cause + " " + best.title))
    rationale = (
        f"Compared title, root cause, affected component, and impact. "
        f"Closest match is {best.issue_id} ({best.title}) with similarity score {best_score:.2f}. "
        f"Shared terms: {', '.join(overlap[:8]) or 'none'}."
    )
    return {
        "verdict": verdict,
        "confidence": confidence,
        "score": round(best_score, 3),
        "matched_issue": dataclasses.asdict(best),
        "rationale": rationale,
    }


def parse_new_issue(issue_text: str, label: str = "ad hoc issue") -> IssueCandidate:
    temp_source = PreparedSource(
        source_id="INLINE",
        input_source=label,
        resolved_source=label,
        source_type="text",
        local_artifact_path="",
        normalized_text_path="",
    )
    extracted = extract_candidates_from_text(issue_text, temp_source)
    if extracted:
        return extracted[0]

    summary = collapse_ws(issue_text)
    title = summary.split(".")[0][:120].strip() or "New issue"
    return IssueCandidate(
        title=title,
        summary=summary,
        root_cause=infer_root_cause(summary, title),
        impact=infer_impact(summary),
        affected_component=infer_component(summary),
        severity=infer_severity(summary),
        source=label,
        aliases=[title],
        source_id="INLINE",
        evidence_snippet=summary[:300],
    )


def parse_issue_candidates(issue_text: str, label: str) -> list[IssueCandidate]:
    temp_source = PreparedSource(
        source_id="INLINE",
        input_source=label,
        resolved_source=label,
        source_type="text",
        local_artifact_path="",
        normalized_text_path="",
    )
    extracted = extract_candidates_from_text(issue_text, temp_source)
    return extracted or [parse_new_issue(issue_text, label)]


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
    issues = apply_llm_deduplication_from_payload(state_payload) or aggregate_candidates(candidates)
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


def run_build(args: argparse.Namespace) -> int:
    workspace_dir = create_workspace(args.workspace_dir)
    prepared_sources, _requested_inputs, _expanded_inputs = prepare_sources(args.input, workspace_dir)
    if args.extractions_file:
        candidates = apply_claude_extractions(prepared_sources, Path(args.extractions_file))
    else:
        candidates: list[IssueCandidate] = []
        for source in prepared_sources:
            text = Path(source.normalized_text_path).read_text(encoding="utf-8")
            extracted = extract_candidates_from_text(text, source)
            if extracted:
                source.extraction_status = "partial"
                source.warnings = dedupe_list(source.warnings + ["Used deterministic fallback extraction instead of Claude-assisted extraction."])
                candidates.extend(extracted)
            else:
                source.extraction_status = "failed"
                source.warnings = dedupe_list(source.warnings + ["No issue candidates extracted from normalized text."])

    existing_sources: list[PreparedSource] = []
    if args.merge_known:
        existing_issues, existing_sources = load_known_payload(Path(args.merge_known))
        candidates = [canonical_issue_to_candidate(issue) for issue in existing_issues] + candidates

    issues = aggregate_candidates(candidates)
    all_sources = merge_source_lists(existing_sources, prepared_sources)
    output_path = Path(args.output)
    json_path = write_outputs(output_path, issues, dedupe_list([source.input_source for source in all_sources]), all_sources)
    print(
        json.dumps(
            {
                "status": "ok",
                "sources": [source.input_source for source in prepared_sources],
                "candidate_count": len(candidates),
                "canonical_issue_count": len(issues),
                "output": str(json_path),
            },
            indent=2,
        )
    )
    return 0


def run_check(args: argparse.Namespace) -> int:
    known_path = Path(args.known)
    issues = load_known_issues(known_path)
    if args.issue_file:
        issue_text = Path(args.issue_file).read_text(encoding="utf-8")
        label = args.issue_file
    else:
        issue_text = args.issue_text
        label = "inline issue"
    candidates = parse_issue_candidates(issue_text, label)
    if len(candidates) == 1:
        result = check_issue(issues, candidates[0])
        result["candidate"] = dataclasses.asdict(candidates[0])
        print(json.dumps(result, indent=2))
        return 0

    results: list[dict[str, Any]] = []
    summary = {"known": 0, "possibly-known": 0, "new": 0}
    for index, candidate in enumerate(candidates, start=1):
        result = check_issue(issues, candidate)
        result["candidate"] = dataclasses.asdict(candidate)
        result["index"] = index
        results.append(result)
        verdict = result.get("verdict", "new")
        if verdict in summary:
            summary[verdict] += 1
    print(
        json.dumps(
            {
                "mode": "batch",
                "input": label,
                "finding_count": len(candidates),
                "summary": summary,
                "results": results,
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

    build = subparsers.add_parser("build", help="build known-issues.json from report sources")
    build.add_argument("--input", action="append", required=True, help="local path or URL to an audit report")
    build.add_argument("--output", default="known-issues.json", help="output JSON path")
    build.add_argument("--workspace-dir", help="directory where downloaded and normalized sources should be written")
    build.add_argument("--extractions-file", help="Claude extraction JSON produced from prepare-build output")
    build.add_argument("--merge-known", help="existing known-issues.json to extend instead of rebuilding from scratch")
    build.set_defaults(func=run_build)

    check = subparsers.add_parser("check", help="check whether a new issue is already known")
    check.add_argument("--known", required=True, help="path to known-issues.json")
    group = check.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue-file", help="path to a file containing the new issue")
    group.add_argument("--issue-text", help="inline issue text")
    check.set_defaults(func=run_check)

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
