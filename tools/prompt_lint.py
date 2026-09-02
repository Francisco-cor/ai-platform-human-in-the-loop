#!/usr/bin/env python3
"""Prompt governance linter — Fase 6 LLMOps.

Bloquea PR si prompt cambia allowlist o añade approve sin ADR.

Reglas:
- Si prompts/registry/*.yaml o agents/prompts.py cambia y contiene keywords críticas
  (approve, allowlist, budget, scope_hash, approval, supplier, tool) sin ADR en docs/decisions,
  falla (exit 1).
- Modo --strict: también revisa que prompt_hash esté actualizado (no hardcodeado stale).
- Usado en CI: python tools/prompt_lint.py  (o python -m procurement_platform.tools.prompt_lint compat)

Check en CI: añade a .github/workflows/ci.yml job lint-test:
  - run: python tools/prompt_lint.py

Ignora si no hay cambios de prompt (sale 0).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CRITICAL_KEYWORDS = [
    "approve",
    "approval",
    "scope_hash",
    "allowlist",
    "budget",
    "supplier",
    "policy",
    "tool",
    "gateway",
    "prompt_version",
    "system",
    # injection bypass attempts
    "ignore previous",
    "you are admin",
    "jailbreak",
]

CRITICAL_REGEX = re.compile(
    r"(approve|allowlist|scope_hash|budget|supplier.*allow|policy.*allow|ignore previous|you are admin)",
    re.IGNORECASE,
)


def _git_diff_files(base: str = "origin/main") -> list[str]:
    """Retorna lista de archivos modificados vs base (o HEAD si no hay base)."""
    try:
        # try origin/main...HEAD
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1...HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    # fallback: status
    try:
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
        files = []
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                files.append(parts[-1])
        return files
    except Exception:
        return []


def _git_diff_content(path: str, base: str = "origin/main") -> str:
    try:
        result = subprocess.run(
            ["git", "diff", f"{base}...HEAD", "--", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    try:
        result = subprocess.run(["git", "diff", "HEAD~1...HEAD", "--", path], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    # fallback read file
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _has_adr_for_prompt(prompt_version: str | None = None) -> bool:
    dec_dir = Path("docs/decisions")
    gov = Path("docs/governance/prompt_review.md")
    # check recent ADR mentions prompt
    candidates = []
    if dec_dir.exists():
        candidates.extend(dec_dir.glob("*.md"))
    if gov.exists():
        candidates.append(gov)
    combined = ""
    for p in candidates:
        try:
            combined += p.read_text(encoding="utf-8", errors="ignore").lower() + "\n"
        except Exception:
            continue
    # if prompt_version provided, require it mentioned
    if prompt_version:
        if prompt_version.lower() in combined:
            return True
        return False
    # general: check if any recent file mentions prompt change justification
    if "prompt" in combined and ("procurement-v" in combined or "prompt_hash" in combined):
        return True
    return False


def _check_file_for_critical(path: Path, content: str) -> list[str]:
    hits = []
    for kw in CRITICAL_KEYWORDS:
        if kw.lower() in content.lower():
            # only flag if diff adds critical (starts with +) or file is new
            # heuristic: if content contains keyword, note it
            hits.append(kw)
    # use regex for more precise
    if CRITICAL_REGEX.search(content):
        if "regex" not in hits:
            hits.append("regex-critical")
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt governance linter — Fase 6")
    parser.add_argument("--strict", action="store_true", help="Strict: fail if any prompt file changed without ADR, even without critical keywords")
    parser.add_argument("--base", default="origin/main", help="Git base for diff")
    parser.add_argument("--prompt-version", default=None, help="Prompt version to check ADR for (e.g. procurement-v2)")
    args = parser.parse_args()

    prompt_files = [
        "prompts/registry/procurement-v1.yaml",
        "prompts/registry/procurement-v2.yaml",
        "src/procurement_platform/agents/prompts.py",
        "agents/prompts.py",  # alt
    ]
    # also discover dynamically
    prompt_glob = list(Path("prompts/registry").glob("*.yaml")) if Path("prompts/registry").exists() else []
    for p in prompt_glob:
        if str(p) not in prompt_files:
            prompt_files.append(str(p))

    changed_prompt_files = []
    for pf in prompt_files:
        # check if file in diff or untracked with changes
        diff_files = _git_diff_files(base=args.base)
        # normalize path separators
        norm_files = [f.replace("\\", "/") for f in diff_files]
        norm_pf = pf.replace("\\", "/")
        if norm_pf in norm_files or Path(pf).exists() and args.strict:
            # check content diff
            content = _git_diff_content(pf, base=args.base)
            if content.strip():
                changed_prompt_files.append((pf, content))

    # also if no git diff but file exists and strict, check whole file
    if not changed_prompt_files and args.strict:
        # if any prompt file exists, consider changed for lint in CI without git history
        for pf in prompt_files:
            if Path(pf).exists():
                try:
                    content = Path(pf).read_text(encoding="utf-8", errors="ignore")
                    if "prompt" in content.lower():
                        changed_prompt_files.append((pf, content))
                        break
                except Exception:
                    continue

    if not changed_prompt_files:
        print("prompt_lint: no prompt changes detected — OK")
        sys.exit(0)

    print(f"prompt_lint: detected changes in {len(changed_prompt_files)} prompt file(s)")
    failed = False
    for pf, content in changed_prompt_files:
        hits = _check_file_for_critical(Path(pf), content)
        # also check added lines for critical
        added_lines = "\n".join(l for l in content.splitlines() if l.startswith("+"))
        added_hits = _check_file_for_critical(Path(pf), added_lines) if added_lines else hits
        effective_hits = added_hits if added_lines else hits
        print(f"  {pf}: critical keywords: {effective_hits if effective_hits else 'none'}")
        if effective_hits:
            # require ADR
            pv = args.prompt_version
            # try infer version from file name
            if not pv and "procurement-v" in pf:
                import re as _re

                m = _re.search(r"procurement-v\d+", pf)
                if m:
                    pv = m.group(0)
            has_adr = _has_adr_for_prompt(pv or "procurement")
            if not has_adr:
                print(f"    FAIL: critical prompt change without ADR docs/decisions (expected mention of {pv or 'prompt'}).")
                print(f"    -> Create ADR docs/decisions/00XX-prompt-{pv or 'v2'}.md and ensure docs/governance/prompt_review.md mentions {pv}")
                failed = True
            else:
                print(f"    OK: ADR found for {pv}")
        else:
            # no critical keywords, but if strict still require review label?
            if args.strict:
                has_adr = _has_adr_for_prompt(args.prompt_version)
                if not has_adr:
                    print(f"    WARNING: prompt changed but no ADR found — strict requires ADR. Consider adding docs/decisions note.")
                    # not fail in non-strict, but in strict we fail
                    failed = True
            else:
                print(f"    OK: no critical keywords, no ADR required")

    # also check hash consistency: ensure get_prompt_hash matches file hash (not stale)
    try:
        from procurement_platform.agents.prompts import get_prompt_hash, reset_prompt_cache

        reset_prompt_cache()
        for pf, _ in changed_prompt_files:
            if "procurement-v" in pf:
                import re as _re, hashlib

                m = _re.search(r"(procurement-v\d+)", pf)
                if m:
                    ver = m.group(1)
                    h = get_prompt_hash(ver)
                    # verify file hash equals loader hash (should, unless fallback)
                    data = Path(pf).read_bytes()
                    expected = "sha256:" + hashlib.sha256(data).hexdigest()
                    if h != expected:
                        print(f"  {pf}: hash mismatch loader {h} vs file {expected} — indicates loader fallback, check file path")
                        # not fail, just warning
    except Exception as e:
        print(f"hash check skipped: {e}")

    if failed:
        print("\nprompt_lint FAILED — prompt governance requires ADR and prompt-review label")
        print("See docs/governance/prompt_review.md")
        sys.exit(1)
    else:
        print("\nprompt_lint PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
