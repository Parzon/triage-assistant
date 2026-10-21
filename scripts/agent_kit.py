#!/usr/bin/env python3
"""The agent kit: the playbook and the skills every coding agent works with
(docs/agent-playbook.md).

    python3 scripts/agent_kit.py check [repo]             every skill will load and is accounted for
    python3 scripts/agent_kit.py copy <repo> [--codex]    the kit into another repository's checkout

An agent skips a skill whose front matter it can't read, without a word, so
`check` runs in `make lint` and CI. `copy` is how the kit travels: into a
team's repository, or from the kit's owner to everyone, as a diff someone
reviews. `--codex` also mirrors the skills into .agents/skills/, where Codex
reads them (kept up to date by every later copy). Standard library only: it
runs on a bare host.
"""

import argparse
import filecmp
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = Path(".claude/skills")
CODEX_SKILLS = Path(".agents/skills")
# Overwritten on every copy: a team changes them through the kit's owner.
KIT_FILES = [Path("docs/agent-playbook.md"), Path("scripts/agent_kit.py"), SKILLS / "README.md"]
# A starting point, copied only where the repository has none.
STARTERS = [Path(".github/pull_request_template.md")]
# The Agent Skills format's limits, which Claude Code, Copilot and Codex share.
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
MAX_NAME, MAX_DESCRIPTION = 64, 1024


def front_matter(text: str) -> dict[str, str] | None:
    # No YAML parser on a bare host: top-level `key: value` lines, with
    # indented lines continuing the value (a `>` or `|` block, or a wrapped one).
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in lines[1:]:
        return None
    fields: dict[str, str] = {}
    key = ""
    for line in lines[1 : lines.index("---", 1)]:
        if line[:1] not in (" ", "\t") and ":" in line:
            key, value = (part.strip() for part in line.split(":", 1))
            fields[key] = value
        elif key and line.strip():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return fields


def description_problem(raw: str) -> str | None:
    if raw.startswith((">", "|")):
        text = raw.lstrip(">|-+ ")
    elif raw[:1] in ("'", '"'):
        text = raw[1:-1] if len(raw) > 1 and raw[-1] == raw[0] else ""
        if not text:
            return "its quoted description doesn't close"
    else:
        text = raw
        if ": " in raw or " #" in raw:
            return 'its description has ": " or " #", which YAML misreads: quote it, or use `>`'
    if not text:
        return "it has no description"
    if len(text) > MAX_DESCRIPTION:
        return f"its description is {len(text)} characters (at most {MAX_DESCRIPTION})"
    return None


def skill_problems(skill: Path, listed: str) -> list[str]:
    where = f"{skill.relative_to(skill.parents[2])}"
    if not (skill / "SKILL.md").is_file():
        return [f"{where}: no SKILL.md"]
    fields = front_matter((skill / "SKILL.md").read_text())
    if fields is None:
        return [f"{where}/SKILL.md: no front matter between two `---` lines"]
    problems = []
    name = fields.get("name", "")
    if name != skill.name:
        problems.append(f"{where}/SKILL.md: name is {name!r}, but the folder is {skill.name!r}")
    if not NAME.fullmatch(skill.name) or len(skill.name) > MAX_NAME:
        problems.append(
            f"{where}: a name is lowercase letters, digits and single hyphens, at most {MAX_NAME}"
        )
    if problem := description_problem(fields.get("description", "")):
        problems.append(f"{where}/SKILL.md: {problem}")
    if f"`{skill.name}`" not in listed:
        problems.append(
            f"{where}: not listed in {SKILLS}/README.md or LOCAL.md (its source, when to use it)"
        )
    return problems


def same_tree(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    return not mismatch and not errors and all(same_tree(a / d, b / d) for d in cmp.common_dirs)


def check(repo: Path) -> int:
    skills_dir = repo / SKILLS
    skills = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    listed = "".join(
        (skills_dir / f).read_text()
        for f in ("README.md", "LOCAL.md")
        if (skills_dir / f).is_file()
    )
    problems = [problem for skill in skills for problem in skill_problems(skill, listed)]
    if (repo / CODEX_SKILLS).is_dir():
        for skill in skills:
            mirror = repo / CODEX_SKILLS / skill.name
            if not mirror.is_dir() or not same_tree(skill, mirror):
                problems.append(f"{CODEX_SKILLS / skill.name}: differs from {SKILLS / skill.name}")
        if any(f"{CODEX_SKILLS}" in p for p in problems):
            problems.append("refresh the Codex copies: python3 scripts/agent_kit.py copy . --codex")
    if problems:
        print("agent kit:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"agent kit: {len(skills)} skills load and are listed")
    return 0


def version() -> str:
    # The playbook's header: "**Version 2, October 2026.**"
    found = re.search(r"\*\*Version ([^*]+?)\.?\*\*", (ROOT / KIT_FILES[0]).read_text())
    return f"version {found.group(1)}" if found else "unversioned"


def replace_tree(src: Path, dst: Path) -> None:
    # .agents/skills linked to .claude/skills: already the same files, and
    # removing the "copy" would remove the skill.
    if dst.resolve() == src.resolve():
        return
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def copy(repo: Path, codex: bool) -> int:
    repo = repo.resolve()
    if not (repo / ".git").exists():
        print(
            f"{repo} is not a git checkout: copy into a clone, so the change is a diff to review",
            file=sys.stderr,
        )
        return 1
    written: list[str] = []
    if repo != ROOT:
        for rel in KIT_FILES:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, repo / rel)
            written.append(str(rel))
        for skill in sorted(p for p in (ROOT / SKILLS).iterdir() if p.is_dir()):
            replace_tree(skill, repo / SKILLS / skill.name)
            written.append(f"{SKILLS / skill.name}/")
        for rel in STARTERS:
            if not (repo / rel).exists():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, repo / rel)
                written.append(f"{rel} (a starting point: put in your own commands)")
    # A repository that already keeps Codex copies gets them refreshed too.
    if codex or (repo != ROOT and (repo / CODEX_SKILLS).is_dir()):
        for skill in sorted(p for p in (repo / SKILLS).iterdir() if p.is_dir()):
            replace_tree(skill, repo / CODEX_SKILLS / skill.name)
        written.append(f"{CODEX_SKILLS}/ (the same skills, for Codex)")
    if not written:
        print(
            "this is the kit's own repository: nothing to copy (--codex refreshes .agents/skills/)"
        )
        return 0
    print(f"agent kit, {version()}, into {repo}:\n  " + "\n  ".join(written))
    if repo != ROOT:
        todo = []
        if not (repo / "AGENTS.md").exists():
            todo.append('AGENTS.md: your commands, rules and traps (the playbook, "Adopt it")')
        if not (repo / "CLAUDE.md").exists():
            todo.append("CLAUDE.md with the line `@AGENTS.md`, for Claude Code")
        todo.append("CI: `python3 scripts/agent_kit.py check` beside your other required checks")
        todo.append("review the diff, then one PR for the whole kit")
        print("next:\n  " + "\n  ".join(todo))
    return check(repo)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="every skill will load and is listed")
    check_cmd.add_argument("repo", nargs="?", type=Path, default=ROOT)
    copy_cmd = commands.add_parser("copy", help="the kit into another repository's checkout")
    copy_cmd.add_argument("repo", type=Path)
    copy_cmd.add_argument(
        "--codex", action="store_true", help="also mirror the skills into .agents/skills/"
    )
    args = parser.parse_args()
    return check(args.repo.resolve()) if args.command == "check" else copy(args.repo, args.codex)


if __name__ == "__main__":
    sys.exit(main())
