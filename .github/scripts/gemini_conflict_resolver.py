#!/usr/bin/env python3
"""
Automated Git merge conflict resolver using Google Gemini API with smart filtering and rate-limit handling.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

MODELS = [
    "gemini-3.1-flash-lite",  # 500 requests/day, 15 RPM on free tier
    "gemini-3-flash",         # 20 requests/day, 5 RPM
    "gemini-2.5-flash-lite",   # 20 requests/day, 10 RPM
    "gemini-2.5-flash",        # 20 requests/day, 5 RPM
]

AUTOMATED_COMMIT_PREFIXES = (
    "style: format",
    "merge: auto-resolve",
    "Merge remote-tracking",
    "Merge branch",
)


def get_merge_base() -> str:
    """Find the common ancestor commit between HEAD and the incoming merge."""
    for ref in ["MERGE_HEAD", "FETCH_HEAD", "upstream/latest"]:
        res = subprocess.run(["git", "merge-base", "HEAD", ref], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return ""


def file_has_custom_fork_commits(file_path: str, merge_base: str) -> bool:
    """Return True if the file was ever touched by custom fork commits, False if only by automated commits."""
    if not merge_base:
        return True
    res = subprocess.run(
        ["git", "log", "--oneline", f"{merge_base}..HEAD", "--", file_path],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return True
    commits = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if not commits:
        return False
    for c in commits:
        msg = " ".join(c.split()[1:])
        if not any(msg.startswith(p) for p in AUTOMATED_COMMIT_PREFIXES):
            return True
    return False


def get_conflicted_files():
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    return files


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
                print(f"Calling {model} (attempt {attempt + 1})...")
                # Throttle to stay safely within RPM limits
                time.sleep(4)
                with urllib.request.urlopen(req, timeout=90) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    return text
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                print(f"HTTPError {e.code} with {model}: {err_body}")
                last_error = e
                if e.code == 429:
                    print("Rate limit hit. Waiting 10s before retry/fallback...")
                    time.sleep(10)
                    continue
                break
            except Exception as e:
                print(f"Request failed with {model}: {e}")
                last_error = e
                time.sleep(2)

    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def clean_resolved_content(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned.rstrip() + "\n"


def resolve_file(file_path: str, api_key: str, merge_base: str) -> bool:
    print(f"\n--- Resolving conflict in: {file_path} ---")

    # 1. Binary or compiled files -> accept upstream
    if file_path.endswith((".mo", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".lock", ".woff", ".woff2")):
        print(f"Auto-resolving compiled/binary file {file_path} from upstream.")
        st = subprocess.run(["git", "status", "--porcelain", file_path], capture_output=True, text=True).stdout[:2]
        if st == "UD":
            subprocess.run(["git", "rm", file_path], check=True)
        else:
            subprocess.run(["git", "checkout", "--theirs", file_path], check=True)
            subprocess.run(["git", "add", file_path], check=True)
        return True

    # 2. Check if file has any custom commits from fork
    if not file_has_custom_fork_commits(file_path, merge_base):
        print(f"Auto-resolving {file_path}: No custom fork commits (only formatting churn). Accepting upstream.")
        st = subprocess.run(["git", "status", "--porcelain", file_path], capture_output=True, text=True).stdout[:2]
        if st == "UD":
            subprocess.run(["git", "rm", file_path], check=True)
        else:
            subprocess.run(["git", "checkout", "--theirs", file_path], check=True)
            subprocess.run(["git", "add", file_path], check=True)
        return True

    # 3. Text file with genuine custom commits -> use Gemini
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"Could not read {file_path}: {e}")
        return False

    if "<<<<<<<" not in content or ">>>>>>>" not in content:
        print(f"No conflict markers found in {file_path}. Skipping.")
        return True

    divergences_text = ""
    try:
        from pathlib import Path
        div_path = Path(__file__).resolve().parent.parent.parent / "FORK_DIVERGENCES.md"
        if div_path.is_file():
            divergences_text = f"\nFork Specifications & Invariants:\n{div_path.read_text(encoding='utf-8')}\n"
    except Exception:
        pass

    prompt = f"""You are an expert software engineer resolving Git merge conflicts for the application Floppy.
File path: {file_path}

Below is the complete file content containing Git merge conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>>`).

Git Context:
- The `HEAD` block contains the local fork's custom features and changes (e.g. cross-category search, collaborator sync, collection bulk add, custom UI/views).
- The incoming branch block (e.g. `upstream/latest`) contains new upstream features, bug fixes, and refactorings.
{divergences_text}

Instructions:
1. Merge both sides intelligently and cleanly.
2. DO NOT discard or lose any of the local fork's features from HEAD.
3. Integrate the upstream improvements and fixes seamlessly.
4. If imports, routes, function arguments, or templates are modified by both, merge them so that both features work without duplicate definitions or syntax errors.
5. Remove all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
6. Output ONLY the raw resolved file content. Do NOT add markdown code fences (like ```python or ```) around the entire output. Output nothing else.

File content:
{content}
"""

    try:
        resolved = call_gemini(prompt, api_key)
        cleaned = clean_resolved_content(resolved)

        if "<<<<<<<" in cleaned or ">>>>>>>" in cleaned:
            print(f"Warning: Gemini output still contains conflict markers in {file_path}!")
            return False

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

        subprocess.run(["git", "add", file_path], check=True)
        print(f"Successfully resolved and staged {file_path}")
        return True
    except Exception as e:
        print(f"Failed to resolve {file_path}: {e}")
        return False


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)

    conflicted_files = get_conflicted_files()
    if not conflicted_files:
        print("No conflicted files found.")
        sys.exit(0)

    merge_base = get_merge_base()
    print(f"Merge base: {merge_base or 'unknown'}")
    print(f"Found {len(conflicted_files)} conflicted file(s):\n" + "\n".join(f"  - {f}" for f in conflicted_files))

    all_resolved = True
    for f in conflicted_files:
        success = resolve_file(f, api_key, merge_base)
        if not success:
            all_resolved = False

    if not all_resolved:
        print("\nError: Could not automatically resolve all conflicted files.")
        sys.exit(1)

    remaining = get_conflicted_files()
    if remaining:
        print(f"Error: {len(remaining)} conflicted files still remain unmerged.")
        sys.exit(1)

    print("\nAll conflicts resolved cleanly. Committing merge...")
    subprocess.run(
        ["git", "commit", "--no-edit", "-m", "merge: auto-resolve upstream conflicts using Gemini"],
        check=True,
    )
    print("Merge commit created successfully.")


if __name__ == "__main__":
    main()
