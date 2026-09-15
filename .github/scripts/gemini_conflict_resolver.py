#!/usr/bin/env python3
"""
Automated Git merge conflict resolver using Google Gemini API.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


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
        for attempt in range(3):
            try:
                print(f"Calling {model} (attempt {attempt + 1})...")
                with urllib.request.urlopen(req, timeout=90) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    return text
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                print(f"HTTPError {e.code} with {model}: {err_body}")
                last_error = e
                if e.code == 429:
                    time.sleep(5)
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


def resolve_file(file_path: str, api_key: str) -> bool:
    print(f"\n--- Resolving conflict in: {file_path} ---")
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"Could not read {file_path}: {e}")
        return False

    if "<<<<<<<" not in content or ">>>>>>>" not in content:
        print(f"No conflict markers found in {file_path}. Skipping.")
        return True

    prompt = f"""You are an expert software engineer resolving Git merge conflicts for the application Floppy.
File path: {file_path}

Below is the complete file content containing Git merge conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>>`).

Git Context:
- The `HEAD` block contains the local fork's custom features and changes (e.g. cross-category search, collaborator sync, collection bulk add, custom UI/views).
- The incoming branch block (e.g. `upstream/latest`) contains new upstream features, bug fixes, and refactorings (e.g. bulk actions, API changes, memory optimizations).

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

    print(f"Found {len(conflicted_files)} conflicted file(s):")
    for f in conflicted_files:
        print(f"  - {f}")

    all_resolved = True
    for f in conflicted_files:
        success = resolve_file(f, api_key)
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
