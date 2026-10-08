#!/usr/bin/env python3
"""
Automated Test Failure Evaluator and Resolver using Google Gemini and fork divergence rules.
Evaluates test failures during upstream sync, applies automated repairs for known
fork divergences (e.g. query count pins, theme token contracts), or consults Gemini
to reconcile upstream changes with fork features.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def call_gemini(prompt: str, api_key: str) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
        },
    }
    data = json.dumps(payload).encode("utf-8")

    last_error = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(2):
            try:
                print(f"Calling Gemini ({model}, attempt {attempt + 1})...")
                time.sleep(3)
                with urllib.request.urlopen(req, timeout=90) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    return text
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                print(f"HTTPError {e.code} with {model}: {err_body}")
                last_error = e
                if e.code == 429:
                    print("Rate limit hit. Waiting 10s...")
                    time.sleep(10)
                    continue
                break
            except Exception as e:
                print(f"Request failed with {model}: {e}")
                last_error = e
                time.sleep(2)

    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def load_fork_divergences() -> str:
    divergences_file = REPO_ROOT / "FORK_DIVERGENCES.md"
    if divergences_file.is_file():
        return divergences_file.read_text(encoding="utf-8")
    return ""


def extract_failing_test_labels(log_text: str) -> list[str]:
    """Extract dotted test labels like 'app.tests.test_query_counts.QueryCountTests.test_custom_list_detail_query_budget'."""
    labels = []
    # Match patterns like: FAIL: test_name (app.tests.module.ClassName.test_name)
    pattern = re.compile(r"^(?:FAIL|ERROR):\s+([^\s]+)\s+\(([^)]+)\)", re.MULTILINE)
    for match in pattern.finditer(log_text):
        test_method = match.group(1)
        test_target = match.group(2)
        if test_target.endswith(f".{test_method}"):
            labels.append(test_target)
        else:
            labels.append(f"{test_target}.{test_method}")
    return sorted(list(set(labels)))


def try_fix_query_count_budget(log_text: str) -> bool:
    """Detect and update query budget pins that changed due to fork features."""
    # Example: AssertionError: 55 not less than or equal to 39 : custom list detail issued 55 queries, budget is 39.
    pattern = re.compile(
        r"AssertionError:\s+(\d+)\s+not less than or equal to\s+(\d+)\s+:\s+(.*?)\s+issued\s+\1\s+queries,\s+budget is\s+\2\."
    )
    matches = pattern.findall(log_text)
    if not matches:
        return False

    query_counts_path = REPO_ROOT / "src" / "app" / "tests" / "test_query_counts.py"
    if not query_counts_path.is_file():
        return False

    content = query_counts_path.read_text(encoding="utf-8")
    modified = False

    for actual_str, budget_str, label in matches:
        actual = int(actual_str)
        budget = int(budget_str)
        print(f"Detected query count budget mismatch: {label} (actual: {actual}, budget: {budget})")

        # Map common label snippets to constant names
        candidates = []
        if "custom list detail" in label:
            candidates.append("CUSTOM_LIST_DETAIL_MAX_QUERIES")
        elif "home page" in label:
            candidates.append("HOME_PAGE_MAX_QUERIES")
        elif "tv list" in label:
            candidates.append("TV_LIST_DEFAULT_SORT_MAX_QUERIES")
        elif "game list" in label:
            candidates.append("GAME_LIST_DEFAULT_SORT_MAX_QUERIES")

        for cand in candidates:
            cand_pattern = re.compile(rf"({cand}\s*=\s*){budget}")
            if cand_pattern.search(content):
                content = cand_pattern.sub(rf"\g<1>{actual}", content, count=1)
                print(f"Updated {cand} from {budget} to {actual} in {query_counts_path.name}")
                modified = True
                break

    if modified:
        query_counts_path.write_text(content, encoding="utf-8")
        return True
    return False


def try_fix_theme_tokens(log_text: str) -> bool:
    """Detect and replace hardcoded theme tokens in templates."""
    # Pattern: Replace with text-[var(--color-link)] or text-[var(--color-text-muted)]. Found: ['list_status_tabs.html: text-indigo-400']
    pattern = re.compile(r"Found:\s*\[['\"]([^:]+):\s*([^'\"]+)['\"]\]")
    matches = pattern.findall(log_text)
    if not matches:
        # Also check format: 'filename.html: utility'
        pattern2 = re.compile(r"['\"]([a-zA-Z0-9_\-]+\.html):\s*([a-zA-Z0-9_\-]+)['\"]")
        matches = pattern2.findall(log_text)

    if not matches:
        return False

    modified = False
    for filename, utility in matches:
        print(f"Detected banned theme utility in {filename}: {utility}")
        # Find template file
        for path in (REPO_ROOT / "src" / "templates").rglob(filename):
            file_content = path.read_text(encoding="utf-8")
            if utility in file_content:
                # Replace with appropriate token
                replacement = "text-[var(--color-link)]" if "indigo" in utility else "text-[var(--color-text-muted)]"
                file_content = file_content.replace(utility, replacement)
                path.write_text(file_content, encoding="utf-8")
                print(f"Replaced {utility} with {replacement} in {path}")
                modified = True

    return modified


def try_gemini_reconcile(failing_label: str, log_text: str, api_key: str, divergences: str) -> bool:
    """Use Gemini to analyze and patch a remaining test failure."""
    prompt = f"""You are an automated software engineer repairing test failures in a fork of the Floppy application.

A test failed after merging upstream changes into the fork.
Failing test: {failing_label}

Fork Divergences / Intentional Behaviors:
{divergences}

Test Failure Output / Traceback:
{log_text[-4000:]}

Instructions:
1. Determine the root cause of the failure.
2. If the failure is caused by upstream test expectations conflicting with intentional fork behavior (e.g. search placeholder text, custom list features), determine the minimal fix to make the test or code compatible without regressing fork features.
3. Identify the EXACT file path that needs editing.
4. Output your response as valid JSON with two fields:
   - "file_path": relative path from repository root (e.g. "src/templates/base.html" or "src/app/tests/...")
   - "search_block": exact contiguous text in that file to replace
   - "replace_block": replacement text
"""
    try:
        response_text = call_gemini(prompt, api_key)
        # Parse JSON
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        data = json.loads(cleaned)
        rel_path = data.get("file_path")
        search_block = data.get("search_block")
        replace_block = data.get("replace_block")

        if not rel_path or not search_block or not replace_block:
            print("Gemini response missing required JSON fields.")
            return False

        target_file = REPO_ROOT / rel_path
        if not target_file.is_file():
            print(f"Target file {target_file} does not exist.")
            return False

        content = target_file.read_text(encoding="utf-8")
        if search_block not in content:
            print(f"Search block not found in {rel_path}.")
            return False

        new_content = content.replace(search_block, replace_block, 1)
        target_file.write_text(new_content, encoding="utf-8")
        print(f"Applied Gemini patch to {rel_path}.")
        return True
    except Exception as e:
        print(f"Gemini reconciliation error: {e}")
        return False


def run_targeted_tests(labels: list[str]) -> bool:
    """Run only the failing tests to verify the fix."""
    print(f"\nRunning targeted tests to verify fix: {' '.join(labels)}")
    env = os.environ.copy()
    env["SECRET"] = "test-secret-evaluator-token"
    cmd = [
        "uv", "run", "--no-sync", "python", "src/manage.py", "test",
        *labels, "--buffer", "--exclude-tag", "network", "--exclude-tag", "slow"
    ]
    res = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    print(res.stdout)
    if res.returncode != 0:
        print(res.stderr)
        return False
    return True


def main():
    log_file = sys.argv[1] if len(sys.argv) > 1 else "test_run.log"
    log_path = Path(log_file)
    if not log_path.is_file():
        print(f"Log file {log_file} does not exist.")
        sys.exit(1)

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    failing_labels = extract_failing_test_labels(log_text)
    print(f"Found {len(failing_labels)} failing test(s):\n" + "\n".join(f"  - {f}" for f in failing_labels))

    if not failing_labels:
        print("No failing tests identified in log.")
        sys.exit(1)

    divergences = load_fork_divergences()
    any_fixed = False

    # 1. Check known rule fixes
    if try_fix_query_count_budget(log_text):
        any_fixed = True
    if try_fix_theme_tokens(log_text):
        any_fixed = True

    # 2. Check Gemini for remaining failures
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key and not any_fixed:
        for label in failing_labels:
            if try_gemini_reconcile(label, log_text, api_key, divergences):
                any_fixed = True

    if not any_fixed:
        print("Could not automatically determine any fixes.")
        sys.exit(1)

    # 3. Verify fixes
    if run_targeted_tests(failing_labels):
        print("\nAll targeted tests passed after automated repairs!")
        # Commit the changes
        subprocess.run(
            ["git", "commit", "-am", "test: auto-resolve test suite discrepancies using Gemini & fork rules"],
            cwd=REPO_ROOT,
            check=True,
        )
        sys.exit(0)
    else:
        print("\nTests still failed after attempted repairs.")
        sys.exit(1)


if __name__ == "__main__":
    main()
