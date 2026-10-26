---
name: session-diff-review
description: >-
  Reviews current session work and git status/diffs to generate an interactive, beautiful HTML report.
  Use when the user asks to summarize session work, review git diffs, analyze modified files and classes
  with Mermaid UML diagrams, break down hunk-by-hunk diffs with copyable IDs for chat follow-ups, and
  suggest alternative implementations.
---

# Session Diff Review Skill

This skill analyzes the current conversation session's context, local `git status`, and `git diff` to generate an interactive, standalone **HTML Review Report**.

---

## What This Skill Delivers

1. **Session & Git Overview**:
   - High-level summary of what was accomplished in the current session.
   - Project, branch, and statistics (files changed, additions, deletions, diff hunks).
2. **Architecture & Class UML Diagrams**:
   - Class roles and responsibilities explained.
   - Interactive Mermaid UML diagram (`classDiagram`) visualizing relationships and modified components.
3. **Hunk-by-Hunk Diffs**:
   - Split by location/hunk even within the same file.
   - Clear colored diff views (+/- lines with line numbers).
   - In-depth explanation of what each diff is doing and why.
4. **Alternative Implementation & Review Insights**:
   - Senior code reviewer suggestions on alternative patterns, edge-case hardening, or cleaner implementations.
5. **Interactive Copy-to-Chat Buttons**:
   - Each diff has a unique ID (e.g. `DIFF-01`, `DIFF-02`).
   - A `📋 ID 복사` (Copy ID) button copies a pre-formatted chat prompt to the clipboard so the user can immediately paste it into the current chat session to ask questions about that exact diff.
6. **Polished Standalone HTML & Browser Auto-Open**:
   - Dark/Light mode toggle, file filters, and collapsible cards.
   - Automatically opens in the default browser using `open <report.html>`.

---

## Workflow Steps

### Step 1: Extract Git Diff Skeleton

Execute the runner script using `run_command` to inspect git status and extract diffs split into hunks:

```bash
python3 ~/.gemini/config/skills/session-diff-review/scripts/generate_report.py extract \
  --repo "<repo_path>" \
  -o "/tmp/session_review_skeleton.json"
```

*Note: If no target is specified, the script inspects unstaged + staged changes (and untracked files). If the working copy is clean, it inspects the most recent commit (`HEAD~1..HEAD`).*

---

### Step 2: Analyze & Enrich Review Data

Read `/tmp/session_review_skeleton.json`. As an expert AI pair programmer and code reviewer, enrich the JSON file with deep contextual understanding:

1. **`overview.summary` & `overview.key_points`**:
   - Summarize the intent, user requests, and solutions developed during this session.
2. **`architecture.diagram`**:
   - Write a valid Mermaid `classDiagram` illustrating the classes/components touched, their inheritance/protocols, and key altered methods.
3. **`architecture.components`**:
   - For each modified file/class, write its primary responsibility (`role`) and the reason it was modified (`why_modified`).
4. **`diffs`**:
   For each hunk item (`DIFF-01`, `DIFF-02`, etc.):
   - `explanation`: Detailed explanation of the logic changes occurring in this specific chunk.
   - `alternative`: Pragmatic alternative approaches, architectural trade-offs, or code quality suggestions.
   - `copy_text`: Pre-formatted chat prompt (e.g., `[DIFF-01] <filename> (<line_range>) 변경점에 관해 질문이 있습니다:\n`).

Save the enriched JSON to `/tmp/session_review_data.json` using `write_file`.

---

### Step 3: Render the Interactive HTML Report

Execute the renderer script using `run_command`:

```bash
python3 ~/.gemini/config/skills/session-diff-review/scripts/generate_report.py render \
  -i "/tmp/session_review_data.json" \
  -o "<workspace_root>/session_review_report.html" \
  --open
```

*The `--open` flag automatically opens the generated HTML file in the user's default browser on macOS.*

---

### Step 4: Report Findings in Chat

Conclude by providing the user with:
1. A concise overview of the session work.
2. A clickable markdown file link to the generated HTML report:
   `[session_review_report.html](file://<workspace_root>/session_review_report.html)`
3. A reminder that they can click the **[📋 ID 복사]** button on any diff card in the HTML report to immediately copy the prompt and paste it into the chat for focused discussion on that change.
