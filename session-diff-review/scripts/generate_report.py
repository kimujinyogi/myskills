#!/usr/bin/env python3
"""
Session Diff Review Report Generator
====================================
Generates an interactive, beautiful HTML report reviewing current session work
and git diffs. Supports hunk-level splitting, unique IDs with copy buttons for
direct chat interaction, UML/class diagrams via Mermaid, and alternative
implementation suggestions.
"""

import argparse
import base64
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_git(args, cwd=None):
    """Run a git command and return its stdout as a string."""
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return res.stdout
    except subprocess.CalledProcessError:
        return ""


def get_git_repo_root(cwd=None):
    root = run_git(["rev-parse", "--show-toplevel"], cwd=cwd).strip()
    return root if root else (cwd or os.getcwd())


def get_untracked_files_diff(cwd=None):
    """Generate pseudo-diffs for untracked files so new files appear in the report."""
    status_output = run_git(["status", "--porcelain"], cwd=cwd)
    untracked_diffs = []
    for line in status_output.splitlines():
        if line.startswith("?? "):
            filepath = line[3:].strip()
            full_path = os.path.join(cwd or os.getcwd(), filepath)
            if os.path.isfile(full_path):
                # Only include text files under a reasonable size (e.g. 500KB)
                if os.path.getsize(full_path) < 500 * 1024:
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        lines = content.splitlines()
                        diff_block = [
                            f"diff --git a/{filepath} b/{filepath}",
                            "new file mode 100644",
                            "--- /dev/null",
                            f"+++ b/{filepath}",
                            f"@@ -0,0 +1,{len(lines)} @@",
                        ]
                        for l in lines:
                            diff_block.append(f"+{l}")
                        untracked_diffs.append("\n".join(diff_block) + "\n")
                    except Exception:
                        pass
    return "\n".join(untracked_diffs)


def parse_git_diff_hunks(diff_text):
    """
    Parses unified git diff output into structured hunks.
    Returns a list of dicts representing each hunk.
    """
    files = []
    current_file = None
    lines = diff_text.splitlines(keepends=True)
    i = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.strip().split()
            old_path = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else ""
            new_path = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else ""
            file_path = new_path or old_path

            current_file = {
                "file_path": file_path,
                "file_name": os.path.basename(file_path),
                "is_new": False,
                "is_deleted": False,
                "hunks": []
            }
            files.append(current_file)
            i += 1
            continue

        if current_file is None:
            i += 1
            continue

        if line.startswith("new file mode "):
            current_file["is_new"] = True
            i += 1
            continue
        elif line.startswith("deleted file mode "):
            current_file["is_deleted"] = True
            i += 1
            continue

        if line.startswith("@@ "):
            match = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)", line)
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1
                context_info = match.group(5).strip()

                hunk_lines = [line]
                parsed_lines = []
                cur_old = old_start
                cur_new = new_start
                adds = 0
                dels = 0

                i += 1
                while i < len(lines):
                    sub_line = lines[i]
                    if sub_line.startswith("diff --git ") or sub_line.startswith("@@ "):
                        break
                    hunk_lines.append(sub_line)
                    raw_content = sub_line[1:].rstrip("\r\n")

                    if sub_line.startswith("+"):
                        adds += 1
                        parsed_lines.append({
                            "type": "add",
                            "old_no": "",
                            "new_no": cur_new,
                            "content": raw_content
                        })
                        cur_new += 1
                    elif sub_line.startswith("-"):
                        dels += 1
                        parsed_lines.append({
                            "type": "del",
                            "old_no": cur_old,
                            "new_no": "",
                            "content": raw_content
                        })
                        cur_old += 1
                    elif sub_line.startswith(" "):
                        parsed_lines.append({
                            "type": "ctx",
                            "old_no": cur_old,
                            "new_no": cur_new,
                            "content": raw_content
                        })
                        cur_old += 1
                        cur_new += 1
                    elif sub_line.startswith("\\"):
                        parsed_lines.append({
                            "type": "meta",
                            "old_no": "",
                            "new_no": "",
                            "content": sub_line.strip()
                        })
                    i += 1

                line_range = f"L{new_start}-L{new_start + max(0, new_count - 1)}"

                hunk_obj = {
                    "hunk_index": len(current_file["hunks"]) + 1,
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "context_info": context_info,
                    "line_range": line_range,
                    "additions": adds,
                    "deletions": dels,
                    "raw_diff": "".join(hunk_lines),
                    "parsed_lines": parsed_lines
                }
                current_file["hunks"].append(hunk_obj)
                continue

        i += 1

    flat_diffs = []
    diff_counter = 1
    total_adds = 0
    total_dels = 0

    for f in files:
        for h in f["hunks"]:
            diff_id = f"DIFF-{diff_counter:02d}"
            diff_counter += 1
            total_adds += h["additions"]
            total_dels += h["deletions"]

            copy_text = f"[{diff_id}] {f['file_path']} ({h['line_range']}) 변경 사항에 대해 질문이 있습니다:\n"

            flat_diffs.append({
                "id": diff_id,
                "file_path": f["file_path"],
                "file_name": f["file_name"],
                "is_new": f["is_new"],
                "is_deleted": f["is_deleted"],
                "hunk_index": h["hunk_index"],
                "context_info": h["context_info"],
                "line_range": h["line_range"],
                "additions": h["additions"],
                "deletions": h["deletions"],
                "raw_diff": h["raw_diff"],
                "parsed_lines": h["parsed_lines"],
                "explanation": "",
                "alternative": "",
                "copy_text": copy_text
            })

    return {
        "files_count": len(files),
        "total_additions": total_adds,
        "total_deletions": total_dels,
        "diffs": flat_diffs,
        "files": [
            {
                "path": f["file_path"],
                "name": f["file_name"],
                "hunks_count": len(f["hunks"]),
                "is_new": f["is_new"],
                "is_deleted": f["is_deleted"]
            }
            for f in files
        ]
    }


def extract_git_data(repo_path=None, diff_target=None, include_untracked=True):
    """
    Extracts git diff and status information from a repository.
    diff_target can be 'HEAD', a branch name, commit hash, etc.
    """
    cwd = repo_path or os.getcwd()
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).strip() or "main"

    if diff_target:
        diff_text = run_git(["diff", diff_target], cwd=cwd)
    else:
        unstaged = run_git(["diff"], cwd=cwd)
        staged = run_git(["diff", "--cached"], cwd=cwd)
        diff_text = staged + ("\n" if staged and unstaged else "") + unstaged
        if include_untracked:
            untracked = get_untracked_files_diff(cwd=cwd)
            if untracked:
                diff_text += ("\n" if diff_text else "") + untracked

        if not diff_text.strip():
            # If working copy is clean, inspect recent commit
            diff_text = run_git(["diff", "HEAD~1..HEAD"], cwd=cwd)

    parsed = parse_git_diff_hunks(diff_text)
    repo_name = os.path.basename(os.path.abspath(get_git_repo_root(cwd)))

    skeleton = {
        "meta": {
            "title": f"세션 작업 및 코드 Diff 리뷰 보고서 - {repo_name}",
            "project": repo_name,
            "branch": branch,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": {
                "files_changed": parsed["files_count"],
                "additions": parsed["total_additions"],
                "deletions": parsed["total_deletions"],
                "diff_chunks": len(parsed["diffs"])
            }
        },
        "overview": {
            "summary": "현재 세션에서 진행된 작업 내역을 요약해주세요.",
            "key_points": [
                "주요 작업 내용 1",
                "주요 작업 내용 2"
            ]
        },
        "architecture": {
            "diagram": (
                "classDiagram\n"
                "  %% 에이전트가 클래스 관계도 및 변경점(UML)을 작성합니다\n"
                "  class ExampleClass {\n"
                "    +exampleMethod()\n"
                "  }\n"
            ),
            "components": [
                {
                    "name": f["name"],
                    "path": f["path"],
                    "role": f"역할: {f['name']}의 담당 업무 설명",
                    "why_modified": "수정 이유: 이번 작업에서 변경된 구체적 이유"
                }
                for f in parsed["files"]
            ]
        },
        "diffs": parsed["diffs"]
    }
    return skeleton


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__PAGE_TITLE__</title>
  <!-- Mermaid.js CDN for Class UML Diagrams -->
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root[data-theme="dark"] {
      --bg-primary: #0b0f19;
      --bg-surface: #151d2f;
      --bg-surface-elevated: #1e293b;
      --bg-subtle: #0b0f1980;
      --border-color: #26334d;
      --border-highlight: #3b4d70;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --accent-light: #818cf8;
      --diff-add-bg: rgba(16, 185, 129, 0.14);
      --diff-add-text: #6ee7b7;
      --diff-add-gutter: #10b981;
      --diff-del-bg: rgba(239, 68, 68, 0.14);
      --diff-del-text: #fca5a5;
      --diff-del-gutter: #ef4444;
      --diff-ctx-text: #cbd5e1;
      --diff-table-bg: #070a12;
      --alt-bg: linear-gradient(135deg, rgba(234, 179, 8, 0.08) 0%, rgba(245, 158, 11, 0.03) 100%);
      --alt-border: rgba(245, 158, 11, 0.3);
      --alt-header: #fcd34d;
      --alt-text: #fef08a;
      --card-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }

    :root[data-theme="light"] {
      --bg-primary: #f8fafc;
      --bg-surface: #ffffff;
      --bg-surface-elevated: #f1f5f9;
      --bg-subtle: #f8fafc80;
      --border-color: #e2e8f0;
      --border-highlight: #cbd5e1;
      --text-main: #0f172a;
      --text-muted: #475569;
      --text-dim: #64748b;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --accent-light: #6366f1;
      --diff-add-bg: #ecfdf5;
      --diff-add-text: #065f46;
      --diff-add-gutter: #059669;
      --diff-del-bg: #fef2f2;
      --diff-del-text: #991b1b;
      --diff-del-gutter: #dc2626;
      --diff-ctx-text: #334155;
      --diff-table-bg: #f8fafc;
      --alt-bg: #fffbeb;
      --alt-border: #fde68a;
      --alt-header: #b45309;
      --alt-text: #92400e;
      --card-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.06);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background-color: var(--bg-primary);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.6;
      padding-bottom: 90px;
      transition: background-color 0.2s ease, color 0.2s ease;
    }

    .container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 24px;
    }

    /* Header */
    .header {
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 14px;
      padding: 28px 32px;
      margin-bottom: 30px;
      box-shadow: var(--card-shadow);
      position: relative;
    }
    .header-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 22px;
    }
    .header-title-wrap h1 {
      font-size: 26px;
      font-weight: 700;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .header-title-wrap .meta-line {
      font-size: 13px;
      color: var(--text-muted);
      margin-top: 8px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    .meta-tag {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      padding: 3px 10px;
      border-radius: 6px;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .theme-toggle-btn {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 6px 14px;
      border-radius: 20px;
      cursor: pointer;
      font-size: 13px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 500;
      transition: all 0.2s;
    }
    .theme-toggle-btn:hover {
      background: var(--border-highlight);
    }

    /* Stats Grid */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
    }
    .stat-card {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 14px 18px;
      display: flex;
      flex-direction: column;
    }
    .stat-label {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
    }
    .stat-value {
      font-size: 24px;
      font-weight: 700;
      margin-top: 4px;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
    }
    .stat-value.add { color: var(--diff-add-gutter); }
    .stat-value.del { color: var(--diff-del-gutter); }

    /* Section Styling */
    .section {
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 26px;
      margin-bottom: 28px;
      box-shadow: var(--card-shadow);
    }
    .section-title {
      font-size: 20px;
      font-weight: 600;
      color: var(--text-main);
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 10px;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 12px;
    }
    .section-desc {
      font-size: 14px;
      color: var(--text-muted);
      margin-bottom: 20px;
    }

    /* Overview */
    .overview-text {
      font-size: 15px;
      color: var(--text-main);
      margin-bottom: 16px;
      white-space: pre-line;
      line-height: 1.7;
    }
    .key-points-list {
      list-style-type: none;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .key-points-list li {
      background: var(--bg-surface-elevated);
      border-left: 3px solid var(--accent);
      padding: 10px 16px;
      border-radius: 0 6px 6px 0;
      font-size: 14px;
    }

    /* Architecture / Components */
    .arch-container {
      display: grid;
      grid-template-columns: 1fr;
      gap: 20px;
    }
    .mermaid-box {
      background: var(--diff-table-bg);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 20px;
      overflow-x: auto;
      text-align: center;
    }
    .components-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 14px;
      margin-top: 10px;
    }
    .component-card {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 14px 16px;
    }
    .component-name {
      font-weight: 600;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 14px;
      color: var(--accent-light);
      margin-bottom: 6px;
      word-break: break-all;
    }
    .component-role {
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 8px;
      line-height: 1.5;
    }
    .component-why {
      font-size: 13px;
      color: var(--text-main);
      background: rgba(99, 102, 241, 0.08);
      border-left: 2px solid var(--accent);
      padding: 6px 10px;
      border-radius: 2px;
      line-height: 1.5;
    }

    /* Controls Bar */
    .controls-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 20px;
    }
    .file-filters {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .filter-btn {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 13px;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .filter-btn:hover {
      background: var(--border-highlight);
      color: var(--text-main);
    }
    .filter-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .action-btn {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 13px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .action-btn:hover {
      background: var(--border-highlight);
    }

    /* Diff Card */
    .diff-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      margin-bottom: 24px;
      overflow: hidden;
      box-shadow: var(--card-shadow);
      transition: border-color 0.2s ease;
    }
    .diff-card:hover {
      border-color: var(--border-highlight);
    }
    .diff-card.collapsed .diff-body {
      display: none;
    }

    /* Diff Header */
    .diff-header {
      background: var(--bg-surface-elevated);
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      cursor: pointer;
      user-select: none;
    }
    .diff-header-left {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .diff-id-badge {
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 13px;
      padding: 4px 10px;
      border-radius: 6px;
      letter-spacing: 0.05em;
    }
    .diff-path {
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-main);
    }
    .diff-lines {
      font-size: 12px;
      color: var(--text-dim);
      font-family: 'SF Mono', Menlo, Consolas, monospace;
    }
    .diff-context-fn {
      font-size: 12px;
      color: var(--accent-light);
      background: rgba(99, 102, 241, 0.12);
      padding: 2px 8px;
      border-radius: 4px;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
    }
    .diff-header-right {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .diff-stat-badge {
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 12px;
      display: inline-flex;
      gap: 6px;
    }
    .diff-stat-add { color: var(--diff-add-gutter); }
    .diff-stat-del { color: var(--diff-del-gutter); }

    /* Copy Button */
    .copy-btn {
      background: #0ea5e918;
      border: 1px solid #0ea5e960;
      color: #0ea5e9;
      padding: 5px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .copy-btn:hover {
      background: #0ea5e930;
      transform: translateY(-1px);
    }
    .copy-btn.copied {
      background: #10b981;
      border-color: #10b981;
      color: #fff;
    }

    /* Explanation & Alternative */
    .diff-explanation-panel {
      padding: 16px 20px;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .exp-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      margin-bottom: 4px;
    }
    .exp-content {
      font-size: 14px;
      color: var(--text-main);
      line-height: 1.6;
      white-space: pre-line;
    }

    /* Alternative Card */
    .alternative-card {
      background: var(--alt-bg);
      border: 1px solid var(--alt-border);
      border-radius: 6px;
      padding: 14px 16px;
      margin-top: 4px;
    }
    .alternative-header {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--alt-header);
      font-weight: 600;
      font-size: 13px;
      margin-bottom: 6px;
    }
    .alternative-content {
      font-size: 13px;
      color: var(--alt-text);
      line-height: 1.6;
      white-space: pre-line;
    }

    /* Diff Table */
    .diff-table-container {
      overflow-x: auto;
      background: var(--diff-table-bg);
    }
    .diff-table {
      width: 100%;
      border-collapse: collapse;
      font-family: 'SF Mono', Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.5;
    }
    .diff-line {
      display: table-row;
    }
    .diff-line:hover {
      background: rgba(125, 125, 125, 0.08);
    }
    .diff-line.line-add {
      background-color: var(--diff-add-bg);
      color: var(--diff-add-text);
    }
    .diff-line.line-del {
      background-color: var(--diff-del-bg);
      color: var(--diff-del-text);
    }
    .diff-line.line-ctx {
      color: var(--diff-ctx-text);
    }
    .diff-line.line-meta {
      color: var(--text-dim);
      font-style: italic;
    }

    .line-no {
      display: table-cell;
      width: 45px;
      padding: 2px 8px;
      text-align: right;
      color: var(--text-dim);
      user-select: none;
      border-right: 1px solid var(--border-color);
      vertical-align: top;
    }
    .line-marker {
      display: table-cell;
      width: 20px;
      padding: 2px 4px;
      text-align: center;
      font-weight: bold;
      user-select: none;
      vertical-align: top;
    }
    .line-marker.add { color: var(--diff-add-gutter); }
    .line-marker.del { color: var(--diff-del-gutter); }
    .line-content {
      display: table-cell;
      padding: 2px 10px;
      white-space: pre-wrap;
      word-break: break-all;
    }

    /* Toast Notification */
    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #1e1b4b;
      color: #e0e7ff;
      border: 1px solid var(--accent);
      padding: 12px 20px;
      border-radius: 8px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
      font-size: 13px;
      font-weight: 500;
      opacity: 0;
      pointer-events: none;
      transform: translateY(10px);
      transition: all 0.25s ease;
      z-index: 9999;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    #toast.show {
      opacity: 1;
      pointer-events: auto;
      transform: translateY(0);
    }

    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-dim);
    }

    @media (max-width: 768px) {
      .container { padding: 16px 12px; }
      .header { padding: 20px 16px; }
      .header-title-wrap h1 { font-size: 20px; }
      .diff-header { flex-direction: column; align-items: flex-start; gap: 10px; }
      .diff-header-right { width: 100%; justify-content: space-between; }
    }
  </style>
</head>
<body>

  <div class="container">
    <!-- Header -->
    <header class="header">
      <div class="header-top">
        <div class="header-title-wrap">
          <h1>⚡ __TITLE__</h1>
          <div class="meta-line">
            <span class="meta-tag">📦 Project: __PROJECT__</span>
            <span class="meta-tag">🌿 Branch: __BRANCH__</span>
            <span class="meta-tag">🕒 Generated: __CREATED_AT__</span>
          </div>
        </div>
        <button class="theme-toggle-btn" id="themeToggleBtn" onclick="toggleTheme()">
          🌓 테마 전환
        </button>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <span class="stat-label">Files Modified</span>
          <span class="stat-value">__FILES_CHANGED__</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Diff Hunks</span>
          <span class="stat-value">__DIFF_CHUNKS__</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Lines Added</span>
          <span class="stat-value add">+__ADDITIONS__</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Lines Removed</span>
          <span class="stat-value del">-__DELETIONS__</span>
        </div>
      </div>
    </header>

    <!-- Section 1: Overview -->
    <section class="section">
      <h2 class="section-title">📝 작업 내용 요약 (Session Overview)</h2>
      <div class="overview-text">__OVERVIEW_SUMMARY__</div>
      __KEY_POINTS_HTML__
    </section>

    <!-- Section 2: Architecture & UML -->
    <section class="section">
      <h2 class="section-title">🏛️ 수정된 컴포넌트 역할 및 클래스 다이어그램 (Architecture & UML)</h2>
      <p class="section-desc">이번 세션에서 변경된 파일 및 클래스들의 역할과 상호작용 구조입니다.</p>

      <div class="arch-container">
        __MERMAID_DIAGRAM_HTML__
        <div class="components-grid">
          __COMPONENTS_HTML__
        </div>
      </div>
    </section>

    <!-- Section 3: Detailed Diffs -->
    <section class="section" style="background: transparent; border: none; padding: 0; box-shadow: none;">
      <div class="controls-bar">
        <div class="file-filters" id="fileFilters">
          <button class="filter-btn active" data-file="ALL">전체 보기 (ALL)</button>
          __FILE_FILTER_BUTTONS__
        </div>
        <div>
          <button class="action-btn" id="toggleAllBtn" onclick="toggleAllDiffs()">📂 전체 접기/펼치기</button>
        </div>
      </div>

      <div id="diffsList">
        __DIFF_CARDS_HTML__
      </div>
    </section>
  </div>

  <!-- Toast Element -->
  <div id="toast">📋 클립보드에 복사되었습니다! 현재 대화창에 붙여넣어 바로 질문하세요.</div>

  <script>
    // Theme toggle
    function toggleTheme() {
      const htmlEl = document.documentElement;
      const current = htmlEl.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      htmlEl.setAttribute('data-theme', next);
      localStorage.setItem('session_review_theme', next);
    }

    // Restore theme from localStorage
    (function() {
      const saved = localStorage.getItem('session_review_theme');
      if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
      }
    })();

    // Initialize Mermaid
    document.addEventListener("DOMContentLoaded", function() {
      if (typeof mermaid !== 'undefined') {
        const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';
        mermaid.initialize({
          startOnLoad: true,
          theme: theme,
          flowchart: { curve: 'basis' }
        });
      }
    });

    // Copy prompt text from data attribute (Base64 UTF-8)
    function decodeBase64Utf8(str) {
      try {
        const binStr = window.atob(str);
        const len = binStr.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
          bytes[i] = binStr.charCodeAt(i);
        }
        return new TextDecoder().decode(bytes);
      } catch (e) {
        return window.atob(str);
      }
    }

    function copyDiffPrompt(button) {
      const b64 = button.getAttribute('data-b64-prompt') || '';
      if (!b64) return;
      const promptText = decodeBase64Utf8(b64);

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(promptText).then(() => {
          showCopySuccess(button);
        }).catch(err => {
          fallbackCopyText(promptText, button);
        });
      } else {
        fallbackCopyText(promptText, button);
      }
    }

    function fallbackCopyText(text, button) {
      const textArea = document.createElement("textarea");
      textArea.value = text;
      textArea.style.position = "fixed";
      textArea.style.opacity = "0";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      try {
        document.execCommand('copy');
        showCopySuccess(button);
      } catch (err) {
        alert("복사 실패: " + err);
      }
      document.body.removeChild(textArea);
    }

    function showCopySuccess(button) {
      const originalHtml = button.innerHTML;
      button.classList.add("copied");
      button.innerHTML = "✓ Copied!";
      
      const toast = document.getElementById("toast");
      toast.classList.add("show");
      
      setTimeout(() => {
        button.classList.remove("copied");
        button.innerHTML = originalHtml;
      }, 2000);

      setTimeout(() => {
        toast.classList.remove("show");
      }, 3000);
    }

    // Toggle single card collapse
    function toggleDiffCard(event, cardId) {
      if (event.target.closest('.copy-btn')) return;
      const card = document.getElementById(cardId);
      if (card) {
        card.classList.toggle('collapsed');
      }
    }

    // Toggle all diff cards
    let allCollapsed = false;
    function toggleAllDiffs() {
      const cards = document.querySelectorAll('.diff-card');
      allCollapsed = !allCollapsed;
      cards.forEach(card => {
        if (allCollapsed) {
          card.classList.add('collapsed');
        } else {
          card.classList.remove('collapsed');
        }
      });
    }

    // Filter cards by file
    const filterButtons = document.querySelectorAll('.filter-btn');
    filterButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        filterButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const selectedFile = btn.getAttribute('data-file');
        const cards = document.querySelectorAll('.diff-card');
        
        cards.forEach(card => {
          const cardFile = card.getAttribute('data-file');
          if (selectedFile === 'ALL' || cardFile === selectedFile) {
            card.style.display = 'block';
          } else {
            card.style.display = 'none';
          }
        });
      });
    });
  </script>
</body>
</html>
"""


def render_html_report(data):
    """
    Renders the structured review data into a rich HTML report.
    """
    meta = data.get("meta", {})
    overview = data.get("overview", {})
    arch = data.get("architecture", {})
    diffs = data.get("diffs", [])

    # Key points
    key_points = overview.get("key_points", [])
    if key_points:
        kp_html = '<ul class="key-points-list">'
        for pt in key_points:
            kp_html += f"<li>{html.escape(pt)}</li>"
        kp_html += "</ul>"
    else:
        kp_html = ""

    # Mermaid diagram
    diagram_code = arch.get("diagram", "").strip()
    if diagram_code:
        mermaid_html = f'<div class="mermaid-box"><pre class="mermaid">\n{html.escape(diagram_code)}\n</pre></div>'
    else:
        mermaid_html = '<div class="mermaid-box" style="color:var(--text-dim);">클래스 다이어그램 정보가 없습니다.</div>'

    # Components list
    components = arch.get("components", [])
    comp_html = ""
    for c in components:
        comp_name = html.escape(c.get("name", ""))
        comp_path = html.escape(c.get("path", ""))
        comp_role = html.escape(c.get("role", ""))
        comp_why = html.escape(c.get("why_modified", ""))

        comp_html += f"""
        <div class="component-card">
          <div class="component-name" title="{comp_path}">{comp_name}</div>
          <div class="component-role">{comp_role}</div>
          <div class="component-why"><strong>수정 이유:</strong> {comp_why}</div>
        </div>
        """

    # File filter buttons
    unique_files = sorted(list({d.get("file_path", "") for d in diffs if d.get("file_path")}))
    filter_btn_html = ""
    for fpath in unique_files:
        fname = os.path.basename(fpath)
        filter_btn_html += f'<button class="filter-btn" data-file="{html.escape(fpath)}">{html.escape(fname)}</button>'

    # Diff cards
    diff_cards_html = ""
    for d in diffs:
        diff_id = d.get("id", "DIFF-??")
        fpath = d.get("file_path", "")
        fname = d.get("file_name", os.path.basename(fpath))
        ctx_fn = d.get("context_info", "")
        lrange = d.get("line_range", "")
        adds = d.get("additions", 0)
        dels = d.get("deletions", 0)
        exp = d.get("explanation", "").strip()
        alt = d.get("alternative", "").strip()
        copy_text = d.get("copy_text", f"[{diff_id}] {fpath} ({lrange}) 변경점에 관해 질문이 있습니다:\n")

        # Base64 encoded prompt text to preserve exact newlines and unicode characters
        b64_copy_text = base64.b64encode(copy_text.encode('utf-8')).decode('ascii')

        # Diff rows
        parsed_lines = d.get("parsed_lines", [])
        rows_html = ""
        for line in parsed_lines:
            ltype = line.get("type", "ctx")
            old_no = line.get("old_no", "")
            new_no = line.get("new_no", "")
            content = html.escape(line.get("content", ""))

            marker = " "
            row_class = "line-ctx"
            if ltype == "add":
                marker = "+"
                row_class = "line-add"
            elif ltype == "del":
                marker = "-"
                row_class = "line-del"
            elif ltype == "meta":
                marker = " "
                row_class = "line-meta"

            rows_html += f"""
            <div class="diff-line {row_class}">
              <span class="line-no">{old_no}</span>
              <span class="line-no">{new_no}</span>
              <span class="line-marker {ltype}">{marker}</span>
              <span class="line-content">{content}</span>
            </div>
            """

        # Context function badge
        ctx_html = f'<span class="diff-context-fn">{html.escape(ctx_fn)}</span>' if ctx_fn else ""

        # Explanation & Alternative
        exp_block = ""
        if exp or alt:
            exp_block = '<div class="diff-explanation-panel">'
            if exp:
                exp_block += f"""
                <div>
                  <div class="exp-title">🔍 변경 내용 상세 설명 (What & Why)</div>
                  <div class="exp-content">{html.escape(exp)}</div>
                </div>
                """
            if alt:
                exp_block += f"""
                <div class="alternative-card">
                  <div class="alternative-header">💡 다른 구현 방법 제안 (Alternative / Review Insight)</div>
                  <div class="alternative-content">{html.escape(alt)}</div>
                </div>
                """
            exp_block += '</div>'

        card_id = f"card-{diff_id}"
        diff_cards_html += f"""
        <div class="diff-card" id="{card_id}" data-file="{html.escape(fpath)}">
          <div class="diff-header" onclick="toggleDiffCard(event, '{card_id}')">
            <div class="diff-header-left">
              <span class="diff-id-badge">{diff_id}</span>
              <span class="diff-path">{html.escape(fname)}</span>
              <span class="diff-lines">{html.escape(lrange)}</span>
              {ctx_html}
            </div>
            <div class="diff-header-right">
              <span class="diff-stat-badge">
                <span class="diff-stat-add">+{adds}</span>
                <span class="diff-stat-del">-{dels}</span>
              </span>
              <button class="copy-btn" data-b64-prompt="{b64_copy_text}" onclick="copyDiffPrompt(this)">
                📋 ID 복사
              </button>
            </div>
          </div>
          <div class="diff-body">
            {exp_block}
            <div class="diff-table-container">
              <div class="diff-table">
                {rows_html}
              </div>
            </div>
          </div>
        </div>
        """

    stats = meta.get("stats", {})
    page_html = HTML_TEMPLATE
    page_html = page_html.replace("__PAGE_TITLE__", html.escape(meta.get("title", "Session Diff Review Report")))
    page_html = page_html.replace("__TITLE__", html.escape(meta.get("title", "Session Diff Review Report")))
    page_html = page_html.replace("__PROJECT__", html.escape(meta.get("project", "")))
    page_html = page_html.replace("__BRANCH__", html.escape(meta.get("branch", "")))
    page_html = page_html.replace("__CREATED_AT__", html.escape(meta.get("created_at", "")))
    page_html = page_html.replace("__FILES_CHANGED__", str(stats.get("files_changed", 0)))
    page_html = page_html.replace("__DIFF_CHUNKS__", str(stats.get("diff_chunks", len(diffs))))
    page_html = page_html.replace("__ADDITIONS__", str(stats.get("additions", 0)))
    page_html = page_html.replace("__DELETIONS__", str(stats.get("deletions", 0)))
    page_html = page_html.replace("__OVERVIEW_SUMMARY__", html.escape(overview.get("summary", "")))
    page_html = page_html.replace("__KEY_POINTS_HTML__", kp_html)
    page_html = page_html.replace("__MERMAID_DIAGRAM_HTML__", mermaid_html)
    page_html = page_html.replace("__COMPONENTS_HTML__", comp_html)
    page_html = page_html.replace("__FILE_FILTER_BUTTONS__", filter_btn_html)
    page_html = page_html.replace("__DIFF_CARDS_HTML__", diff_cards_html or '<div class="empty-state">변경 내역이 없습니다.</div>')

    return page_html


def main():
    parser = argparse.ArgumentParser(description="Session Diff Review HTML Generator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: extract
    extract_parser = subparsers.add_parser("extract", help="Extract git diffs and build JSON skeleton")
    extract_parser.add_argument("--repo", default=None, help="Path to git repository")
    extract_parser.add_argument("--target", default=None, help="Diff target, e.g. HEAD, HEAD~1, origin/main")
    extract_parser.add_argument("-o", "--output", required=True, help="Path to output json skeleton file")

    # Command: render
    render_parser = subparsers.add_parser("render", help="Render JSON review data into standalone HTML")
    render_parser.add_argument("-i", "--data", required=True, help="Path to JSON review data file")
    render_parser.add_argument("-o", "--output", required=True, help="Path to output HTML file")
    render_parser.add_argument("--open", action="store_true", help="Open the HTML report in default browser")

    # Command: quick
    quick_parser = subparsers.add_parser("quick", help="Directly generate HTML report from current git diff")
    quick_parser.add_argument("--repo", default=None, help="Path to git repository")
    quick_parser.add_argument("--target", default=None, help="Diff target")
    quick_parser.add_argument("-o", "--output", required=True, help="Path to output HTML file")
    quick_parser.add_argument("--open", action="store_true", help="Open the HTML report in default browser")

    args = parser.parse_args()

    if args.command == "extract":
        data = extract_git_data(repo_path=args.repo, diff_target=args.target)
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(json.dumps({
            "status": "success",
            "message": f"Extracted {len(data['diffs'])} diff hunks into {out_path}",
            "output_json": str(out_path),
            "files_count": data["meta"]["stats"]["files_changed"],
            "diff_chunks": data["meta"]["stats"]["diff_chunks"]
        }))

    elif args.command == "render":
        with open(args.data, "r", encoding="utf-8") as f:
            data = json.load(f)
        html_content = render_html_report(data)
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(json.dumps({
            "status": "success",
            "message": f"Report successfully rendered to {out_path}",
            "output_html": str(out_path)
        }))

        if args.open:
            subprocess.run(["open", str(out_path)])

    elif args.command == "quick":
        data = extract_git_data(repo_path=args.repo, diff_target=args.target)
        html_content = render_html_report(data)
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(json.dumps({
            "status": "success",
            "message": f"Quick report generated at {out_path}",
            "output_html": str(out_path)
        }))

        if args.open:
            subprocess.run(["open", str(out_path)])


if __name__ == "__main__":
    main()
