# claude-recall

Context files such as `CLAUDE.md` and `AGENTS.md` are loaded into every session, so they have to stay short. Everything that does not fit there (reference notes, decision records, lessons from past failures, per-project memory, skills) ends up spread over dozens of folders, and the agent only sees the part that belongs to the current project. claude-recall is a small command-line tool that indexes all of those notes into one local SQLite full-text index and answers "have we seen this before?" with a list of titles and file locations. The agent then reads only the files that look relevant. It is a companion to context files, not a replacement: context files hold the rules that must always apply, and recall finds the rest on demand.

Single Python file, standard library only, no network access.

## Why I built it

I run a personal agent harness on Claude Code: a global `CLAUDE.md`, a folder of topic notes, numbered decision records, one-file-per-lesson failure and success notes, and per-project memory. After a few months the problems were concrete:

- Memory was split across 51 project folders (241 files). A lesson learned in one project was invisible from another.
- The same problem was written down in different words. For one recurring issue (a command hanging on an OS dialog), the notes used (in Japanese) "stuck" in 42 files, "dialog" in 21, "hang" in 18, "modal" in 11 and "freeze" in 0. A search for any single word missed most of them.
- Session transcripts were being deleted after Claude Code's default 30-day retention, so older context was gone entirely (fixed separately by raising `cleanupPeriodDays`).

## What it does

- Indexes Markdown notes into SQLite FTS5 with the trigram tokenizer, split by heading, so a hit points to a section and a line number.
- Takes several terms at once and treats them as OR. Files are ranked by how many distinct terms they match, then by occurrences, heading matches, and a per-kind weight (curated lessons and decisions rank above raw notes).
- Updates the index incrementally on every search (only files whose mtime or size changed are re-read).
- Prints titles and paths only, never note bodies. `--snippet` adds one matching line.
- Searches raw session transcripts (`*.jsonl`) separately with `--logs`, listing files newest first.
- Records each search, and optional `--mark hit|miss` feedback, to a JSONL log; `--stats` summarizes it.
- Creates lesson files from a template with `--new failure|success <slug>`.

### What gets indexed

| Kind | Source (defaults) |
|---|---|
| `context` | `~/.claude/CLAUDE.md`, `~/.claude/AGENTS.md` |
| `refs`, `adr`, `lesson`, `command`, `agent` | `~/.claude/refs/`, `decisions/`, `lessons/`, `commands/`, `agents/` (each skipped if missing; configurable) |
| `skill` | `~/.claude/skills/*/SKILL.md`, and `.claude/skills/*/SKILL.md` inside project roots |
| `memory` | `~/.claude/projects/*/memory/*.md` (Claude Code's per-project memory) |
| `project` | `CLAUDE.md`, `AGENTS.md` and `notes/**/*.md` under each configured project root |

Files over 400 KB and files named `_template*` are skipped. `node_modules`, virtualenvs, build output and dot-directories (other than `.claude`) are not walked.

## Install

Requires Python 3.10+ with SQLite 3.34 or newer (for the trigram tokenizer). Check with `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`.

```bash
pipx install git+https://github.com/Barusamico3839/claude-recall
# or, without installing:
curl -O https://raw.githubusercontent.com/Barusamico3839/claude-recall/main/claude_recall.py
python3 claude_recall.py --help
```

## Usage

```bash
recall hang freeze stuck dialog modal     # pass 3-5 synonyms; any match counts
recall font typeface --kind refs,adr      # filter by kind
recall cache --project api --snippet      # filter by project name substring, show a matching line
recall --logs "permission denied"         # search session transcripts, newest first
recall --mark miss --note "what I wanted" # the last search did not find it
recall --mark hit                         # the last search helped
recall --stats                            # last 30 days: searches, zero-hit queries, hits, misses
recall --new failure flaky-ci-cache       # create lessons/YYYY-MM-DD-flaky-ci-cache.md
recall --sources                          # list what would be indexed
recall --reindex                          # rebuild the index from scratch
```

Example output:

```
3 files (index: 3 files, 0 updated)  terms: modal / dialog / freeze
 1. [refs] 2026-03-02 global | Browser notes > Modal dialog
    ~/.claude/refs/browser.md:3  (matched: dialog, modal)
 2. [lesson] 2026-02-01 global | Terminal stuck after opening a file > Cause
    ~/.claude/lessons/2026-02-01-frozen-terminal.md:8  (matched: dialog, freeze, +1 more sections)
 3. [adr] 2026-01-15 global | 0007: Wrap the open command
    ~/.claude/decisions/0007-use-wrapper.md:1  (matched: dialog)
```

### Using it from an agent

Add a line like this to your `CLAUDE.md` or `AGENTS.md`:

```markdown
- Before starting a task, and when stuck, run `recall <3-5 synonyms>` and read the top hits.
  Record failures and successes with `recall --new failure|success <slug>`, and put synonyms in `tags:`.
```

## Configuration

Everything has a default that matches the Claude Code layout, so no configuration is needed to start. Settings are resolved in this order: command-line flag, environment variable, config file, default.

| Setting | Flag / environment variable | Default |
|---|---|---|
| Claude directory | `--claude-dir`, `RECALL_CLAUDE_DIR`, `CLAUDE_CONFIG_DIR` | `~/.claude` |
| Config file | `--config`, `RECALL_CONFIG` | `<claude-dir>/recall.json` |
| Project roots | `RECALL_PROJECT_ROOTS` (separated by `:`; `;` on Windows) or `project_roots` | none |
| Index database | `RECALL_DB` or `db` | `<claude-dir>/cache/recall.db` |
| Search log | `RECALL_LOG` or `log` | `<claude-dir>/state/recall-log.jsonl` |
| Lessons folder | `lessons_dir` | `<claude-dir>/lessons` |

`collections` in the config file replaces the default list of extra folders. Relative paths are resolved against the Claude directory. See [`recall.example.json`](recall.example.json).

The index is a cache: deleting it is safe, and the next search rebuilds it.

## Design decisions

**Full-text search instead of RAG / embeddings.** Measured on my own notes before writing any code: the curated notes were 390 files, 1.5 MB, and a plain text scan over all of them took 0.013 s. Even 2.0 GB of session transcripts took 2.0 s with ripgrep. At this size retrieval speed is not the problem. The one problem embeddings would solve is different wording for the same thing, and that can be handled more cheaply by passing several synonyms per query and writing synonyms into each lesson's `tags:` line. Embeddings would also mean either sending notes (which mention private projects and other people) to an external API, or maintaining a local model.

**Trigram tokenizer.** My notes are mostly Japanese, which has no spaces between words, so word tokenizers do not work. Trigrams match any substring of 3+ characters in any language. Terms shorter than 3 characters fall back to a `LIKE` scan.

**Titles and paths only.** Transcripts and some notes contain untrusted text (web pages, emails, tool output). Automatically injecting matching bodies into the agent's context would also inject any instructions hidden in them. Returning locations keeps the agent's choice of what to read explicit. For the same reason `--logs` lists files and prints a warning.

**Measure before adding more.** `--mark miss` and `--stats` exist to decide, from data, whether semantic search is ever needed. My threshold: more than about 5 misses a month caused by different wording.

**No hook that searches on every prompt.** Considered and rejected: it adds noise to every turn and widens the injection surface. The instruction in `CLAUDE.md` to search before a task has been enough so far.

Measured on the author's machine (WSL2, Python 3.12, SQLite 3.45), indexing a real harness of 630 files / 4.1 MB / 3,782 sections:

| Operation | Time |
|---|---|
| Full rebuild | 2.3 s |
| Incremental check, nothing changed | 0.27 s |
| Query with 4 terms (in-process) | 4 ms |
| `recall` CLI end to end | 0.30 s |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests build a temporary Claude directory with fictional notes and cover source discovery, OR-ranking, filters, short terms, incremental updates, transcript search (with and without ripgrep/grep), lesson creation and the search log. CI runs them on Linux and macOS with Python 3.10, 3.12 and 3.13.

## License

MIT. See [LICENSE](LICENSE).

## 日本語

claude-recall は、Claude Code などの AI エージェントが残す記録（CLAUDE.md・AGENTS.md、プロジェクトごとのメモリ、スキル、参考メモ、判断の記録、失敗と成功の学び）を、1つのローカルの全文検索の索引（SQLite FTS5・trigram）から横断で引く道具です。結果は題名と場所だけを出し、本文はエージェントが選んで読みます。外には何も送りません。

ベクトル検索（RAG）は入れていません。記録は 390 ファイル・1.5 MB で、全文の文字検索が 0.013 秒でした。言葉のばらつき（同じ現象が「固まる」「ハング」「ダイアログ」など別の言葉で書かれる）は、類語を3〜5個まとめて引くことと、学びの記録に類語のタグを書くことで吸収しています。見つからなかった検索は `--mark miss` で残し、月に5件を超えたらベクトル検索を検討する、という決まりで運用しています。
