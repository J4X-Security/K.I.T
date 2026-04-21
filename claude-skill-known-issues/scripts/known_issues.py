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
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


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


@dataclasses.dataclass
class SourceDocument:
    source: str
    content: str


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


def fetch_source(source: str) -> SourceDocument:
    if re.match(r"^https?://", source, re.IGNORECASE):
        request = urllib.request.Request(
            source,
            headers={
                "User-Agent": "known-issues-aggregator/1.0",
                "Accept": "text/plain,text/html,application/json,text/markdown,*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                content = response.read().decode(charset, errors="replace")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"failed to fetch {source}: {exc}") from exc
        return SourceDocument(source=source, content=content)

    path = Path(source)
    if not path.exists():
        raise RuntimeError(f"input path does not exist: {source}")
    return SourceDocument(source=str(path), content=path.read_text(encoding="utf-8"))


def clean_text(content: str, source: str) -> str:
    lower_source = source.lower()
    stripped = content.strip()
    if lower_source.endswith(".json") or stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        strings = list(iter_json_strings(parsed))
        return "\n".join(strings)

    if "<html" in content.lower() or "</p>" in content.lower() or "</div>" in content.lower():
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


def extract_candidates(doc: SourceDocument) -> list[IssueCandidate]:
    text = clean_text(doc.content, doc.source)
    sections = split_sections(text)
    candidates = [candidate for candidate in (section_to_candidate(section, doc.source) for section in sections) if candidate]
    if candidates:
        return candidates
    fallback = fallback_candidates(text, doc.source)
    return fallback


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


def section_to_candidate(section: str, source: str) -> IssueCandidate | None:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if not lines:
        return None

    title = normalize_title(lines[0])
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    summary = extract_labeled(body, ("summary", "description", "issue", "finding"))
    if not summary:
        summary = first_paragraph(body)

    root_cause = extract_labeled(body, ("root cause", "cause", "vulnerability", "bug"))
    if not root_cause:
        root_cause = infer_root_cause(body, title)

    impact = extract_labeled(body, ("impact", "risk", "consequence"))
    if not impact:
        impact = infer_impact(body)

    component = extract_labeled(body, ("affected component", "component", "module", "contract", "function"))
    if not component:
        component = infer_component(section)

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
        source=source,
        aliases=[title],
    )


def fallback_candidates(text: str, source: str) -> list[IssueCandidate]:
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
                source=source,
                aliases=[title],
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
    first = parts[0].lower()
    rest = "".join(part[:1].upper() + part[1:].lower() for part in parts[1:])
    return first + rest


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
    key_source = f"{component}:{cause}"
    digest = hashlib.sha1(key_source.encode("utf-8")).hexdigest()[:10]
    prefix = "-".join(part for part in [component[:20], cause[:28]] if part).strip("-")
    prefix = prefix or slugify(candidate.title)[:32] or "issue"
    return f"{prefix}-{digest}"


def aggregate_candidates(candidates: list[IssueCandidate]) -> list[CanonicalIssue]:
    canonicals: list[CanonicalIssue] = []
    for candidate in candidates:
        match_index, _score = best_match(candidate, canonicals)
        if match_index is None or _score < 0.68:
            issue_id = f"KI-{len(canonicals) + 1:03d}"
            canonicals.append(
                CanonicalIssue(
                    issue_id=issue_id,
                    title=candidate.title,
                    summary=candidate.summary,
                    root_cause=candidate.root_cause,
                    impact=candidate.impact,
                    affected_component=candidate.affected_component,
                    severity=candidate.severity,
                    aliases=dedupe_list(candidate.aliases),
                    source_reports=[candidate.source],
                    canonical_key=canonical_key_for(candidate),
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
            )
        )

    canonicals.sort(key=lambda issue: (severity_rank(issue.severity), issue.title.lower()))
    for index, issue in enumerate(canonicals, start=1):
        issue.issue_id = f"KI-{index:03d}"
    return canonicals


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
    seq = SequenceMatcher(None, left_norm, right_norm).ratio()
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
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def render_known_issues(issues: list[CanonicalIssue], inputs: list[str]) -> str:
    lines = [
        "# Known Issues",
        "",
        "Canonical issue register generated from prior audit sources.",
        "",
        f"- Source count: {len(inputs)}",
        f"- Canonical issue count: {len(issues)}",
        "",
        "## Summary",
        "",
        "| ID | Severity | Component | Title | Sources |",
        "| --- | --- | --- | --- | --- |",
    ]
    for issue in issues:
        lines.append(
            f"| {issue.issue_id} | {issue.severity} | {escape_pipes(issue.affected_component)} | {escape_pipes(issue.title)} | {len(issue.source_reports)} |"
        )

    for issue in issues:
        lines.extend(
            [
                "",
                f"## {issue.issue_id}: {issue.title}",
                "",
                f"- Severity: {issue.severity}",
                f"- Affected component: {issue.affected_component}",
                f"- Canonical key: `{issue.canonical_key}`",
                f"- Sources: {', '.join(issue.source_reports)}",
                "",
                "### Summary",
                "",
                wrap_markdown(issue.summary),
                "",
                "### Root Cause",
                "",
                wrap_markdown(issue.root_cause),
                "",
                "### Impact",
                "",
                wrap_markdown(issue.impact),
                "",
                "### Aliases",
                "",
                ", ".join(issue.aliases) if issue.aliases else "None",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def escape_pipes(text: str) -> str:
    return text.replace("|", "\\|")


def wrap_markdown(text: str) -> str:
    return "\n".join(textwrap.wrap(text, width=100)) if len(text) > 100 else text


def write_outputs(output_path: Path, issues: list[CanonicalIssue], inputs: list[str]) -> tuple[Path, Path]:
    markdown = render_known_issues(issues, inputs)
    output_path.write_text(markdown, encoding="utf-8")
    json_path = output_path.with_suffix(".json")
    payload = {
        "inputs": inputs,
        "issues": [dataclasses.asdict(issue) for issue in issues],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path, json_path


def load_known_issues(known_path: Path) -> list[CanonicalIssue]:
    json_path = known_path.with_suffix(".json")
    if json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        return [CanonicalIssue(**issue) for issue in payload.get("issues", [])]
    return parse_known_issues_markdown(known_path.read_text(encoding="utf-8"))


def parse_known_issues_markdown(content: str) -> list[CanonicalIssue]:
    pattern = re.compile(r"(?ms)^##\s+(KI-\d+):\s+(.+?)\n(.*?)(?=^##\s+KI-\d+:|\Z)")
    issues: list[CanonicalIssue] = []
    for issue_id, title, body in pattern.findall(content):
        severity = extract_simple_bullet(body, "Severity") or "unspecified"
        component = extract_simple_bullet(body, "Affected component") or "Unspecified component"
        key = extract_simple_bullet(body, "Canonical key").strip("`") if extract_simple_bullet(body, "Canonical key") else slugify(title)
        sources = [item.strip() for item in (extract_simple_bullet(body, "Sources") or "").split(",") if item.strip()]
        summary = extract_section(body, "Summary")
        root_cause = extract_section(body, "Root Cause")
        impact = extract_section(body, "Impact")
        aliases_blob = extract_section(body, "Aliases")
        aliases = [item.strip() for item in aliases_blob.split(",") if item.strip()] if aliases_blob else [title]
        issues.append(
            CanonicalIssue(
                issue_id=issue_id,
                title=title.strip(),
                summary=summary or title.strip(),
                root_cause=root_cause or title.strip(),
                impact=impact or "Impact not available.",
                affected_component=component,
                severity=severity.lower(),
                aliases=aliases,
                source_reports=sources,
                canonical_key=key,
            )
        )
    return issues


def extract_simple_bullet(body: str, label: str) -> str:
    match = re.search(rf"(?im)^-\s*{re.escape(label)}:\s*(.+)$", body)
    return match.group(1).strip() if match else ""


def extract_section(body: str, title: str) -> str:
    match = re.search(rf"(?ms)^###\s+{re.escape(title)}\s*\n+(.+?)(?=^###\s+|\Z)", body)
    return collapse_ws(match.group(1)) if match else ""


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
    extracted = extract_candidates(SourceDocument(source=label, content=issue_text))
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
    )


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def run_build(args: argparse.Namespace) -> int:
    candidates: list[IssueCandidate] = []
    sources: list[str] = []
    for raw_source in args.input:
        doc = fetch_source(raw_source)
        sources.append(doc.source)
        extracted = extract_candidates(doc)
        if not extracted:
            print(f"warning: no issue candidates extracted from {doc.source}", file=sys.stderr)
            continue
        candidates.extend(extracted)

    issues = aggregate_candidates(candidates)
    output_path = Path(args.output)
    markdown_path, json_path = write_outputs(output_path, issues, sources)
    print(
        json.dumps(
            {
                "status": "ok",
                "sources": sources,
                "candidate_count": len(candidates),
                "canonical_issue_count": len(issues),
                "markdown": str(markdown_path),
                "json": str(json_path),
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
    candidate = parse_new_issue(issue_text, label)
    result = check_issue(issues, candidate)
    result["candidate"] = dataclasses.asdict(candidate)
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build known-issues.md and known-issues.json from report sources")
    build.add_argument("--input", action="append", required=True, help="local path or URL to an audit report")
    build.add_argument("--output", default="known-issues.md", help="output markdown path")
    build.set_defaults(func=run_build)

    check = subparsers.add_parser("check", help="check whether a new issue is already known")
    check.add_argument("--known", required=True, help="path to known-issues.md")
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
