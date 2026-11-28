"""Merge flow dry run: precheck, fix order and pair-merge tree on a real git repo."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # chain/: merge_flow.py

import merge_flow as cli  # noqa: I001 - after the sys.path line on purpose
from merge_flow import Head, _gh_open_prs, changed_files, clash, jaccard, plan, ready_prs


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    (tmp_path / "b.txt").write_text("alpha\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _pr(repo: Path, number: int, path: str, text: str) -> Head:
    _git(repo, "checkout", "-q", "-b", f"pr{number}", "main")
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"pr {number}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    base = _git(repo, "rev-parse", "main")
    return Head(number, sha, changed_files(repo, base, sha))


def _raw_pr(repo: Path, number: int, path: bytes, content: bytes) -> str:
    base = _git(repo, "rev-parse", "main")
    _git_bytes(repo, b"read-tree", base.encode("ascii"))
    blob = _git_bytes(repo, b"hash-object", b"-w", b"--stdin", input=content).strip()
    _git_bytes(repo, b"update-index", b"--add", b"--cacheinfo", b"100644," + blob + b"," + path)
    tree = _git_bytes(repo, b"write-tree").strip()
    sha = (
        _git_bytes(
            repo,
            b"commit-tree",
            tree,
            b"-p",
            base.encode("ascii"),
            b"-m",
            f"pr {number}".encode("ascii"),
        )
        .strip()
        .decode("ascii")
    )
    _git_bytes(repo, b"read-tree", base.encode("ascii"))
    return sha


def _git_bytes(repo: Path, *args: bytes, input: bytes | None = None) -> bytes:
    return subprocess.run(
        [b"git", b"-C", os.fsencode(repo), *args], check=True, capture_output=True, input=input
    ).stdout


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return (
        subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer], check=False).returncode
        == 0
    )


def test_disjoint_prs_land_in_one_window_commit(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "b.txt", "ALPHA\n")

    result = plan(repo, base, [a, c])

    assert result["clashes"] == []
    assert result["needs_fix"] == []
    assert result["landed"] == [1, 3]
    window = result["window_commit"]
    assert _is_ancestor(repo, base, window)
    assert _is_ancestor(repo, a.sha, window) and _is_ancestor(repo, c.sha, window)
    assert _git(repo, "show", f"{window}:a.txt").startswith("ONE")
    assert _git(repo, "show", f"{window}:b.txt") == "ALPHA"


def test_a_clash_marks_the_later_pr_for_a_fix_and_the_rest_still_land(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    b = _pr(repo, 2, "a.txt", "uno\ntwo\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "b.txt", "ALPHA\n")

    result = plan(repo, base, [a, b, c])

    assert [x["prs"] for x in result["clashes"]] == [[1, 2]]
    assert result["clashes"][0]["files"] == ["a.txt"]
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]
    assert result["landed"] == [1, 3]
    assert not _is_ancestor(repo, b.sha, result["window_commit"])


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [("node", "node/child.txt"), ("node/child.txt", "node")],
)
def test_file_directory_clash_marks_later_pr_for_a_fix(repo: Path, first_path: str, second_path: str) -> None:
    base = _git(repo, "rev-parse", "main")
    first = _pr(repo, 1, first_path, "first\n")
    second = _pr(repo, 2, second_path, "second\n")

    result = plan(repo, base, [first, second])

    assert any(entry["prs"] == [1, 2] for entry in result["clashes"])
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]
    assert result["held"] == []


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [("café", "café/child.txt"), ("café/child.txt", "café")],
)
def test_non_ascii_file_directory_clash_marks_later_pr_for_a_fix(repo: Path, first_path: str, second_path: str) -> None:
    _git(repo, "config", "core.quotePath", "true")
    base = _git(repo, "rev-parse", "main")
    first = _pr(repo, 1, first_path, "first\n")
    second = _pr(repo, 2, second_path, "second\n")

    result = plan(repo, base, [first, second])

    assert any(
        entry["prs"] == [1, 2] and entry["files"] and all(path.startswith("café") for path in entry["files"])
        for entry in result["clashes"]
    )
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]
    assert result["held"] == []


def test_changed_files_returns_non_ascii_path_unquoted(repo: Path) -> None:
    _git(repo, "config", "core.quotePath", "true")
    changed = _pr(repo, 1, "café", "contents\n")

    assert changed.files == frozenset({"café"})


def test_leading_space_path_clash_marks_later_pr_for_a_fix(repo: Path) -> None:
    path = " shared.txt"
    (repo / path).write_text("base\nmiddle\nend\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", "add shared path")
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "pr1", "main")
    (repo / path).write_text("first\nmiddle\nend\n")
    (repo / "\tprior.txt").write_text("prior\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr 1")
    first_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    first = Head(1, first_sha, changed_files(repo, base, first_sha))
    second = _pr(repo, 2, path, "second\nmiddle\nend\n")

    result = plan(repo, base, [first, second])

    assert any(entry["prs"] == [1, 2] and entry["files"] == [path] for entry in result["clashes"])
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]


def test_changed_files_preserves_carriage_return_in_path(repo: Path) -> None:
    changed = _pr(repo, 1, "carriage\rreturn.txt", "contents\n")

    assert changed.files == frozenset({"carriage\rreturn.txt"})


def test_non_utf8_path_round_trips_through_changed_files_and_clash(repo: Path) -> None:
    raw_path = b"invalid-\xff.txt"
    _git(repo, "config", "core.quotePath", "true")
    base = _git(repo, "rev-parse", "main")
    first_sha = _raw_pr(repo, 1, raw_path, b"first\nmiddle\nend\n")
    second_sha = _raw_pr(repo, 2, raw_path, b"second\nmiddle\nend\n")

    first_files = changed_files(repo, base, first_sha)
    second_files = changed_files(repo, base, second_sha)

    assert first_files == second_files == frozenset({os.fsdecode(raw_path)})
    assert clash(repo, first_sha, second_sha) == [os.fsdecode(raw_path)]


def test_unrelated_paths_skip_the_pair_clash_check(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _git(repo, "rev-parse", "main")
    first = _pr(repo, 1, "one.txt", "one\n")
    second = _pr(repo, 2, "two.txt", "two\n")
    pair_checks: list[tuple[str, str]] = []
    original_clash = cli.clash

    def record_pair_check(repo_path: Path, x: str, y: str) -> list[str] | None:
        if {x, y} == {first.sha, second.sha}:
            pair_checks.append((x, y))
        return original_clash(repo_path, x, y)

    monkeypatch.setattr(cli, "clash", record_pair_check)
    result = plan(repo, base, [first, second])

    assert pair_checks == []
    assert result["clashes"] == []
    assert result["needs_fix"] == []


def test_fixes_chain_when_a_pr_clashes_with_one_that_needs_a_fix(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    b = _pr(repo, 2, "a.txt", "uno\nTWO\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "a.txt", "one\ndos\nthree\nfour\nfive\n")

    result = plan(repo, base, [a, b, c])

    assert result["needs_fix"] == [{"pr": 2, "depth": 1}, {"pr": 3, "depth": 2}]
    assert result["fix_rounds"] == 2


def test_a_pr_that_clashes_with_main_needs_a_fix(repo: Path) -> None:
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    (repo / "a.txt").write_text("won\ntwo\nthree\nfour\nfive\n")
    _git(repo, "commit", "-q", "-am", "main moves")
    base = _git(repo, "rev-parse", "main")

    result = plan(repo, base, [a])

    assert result["clashes"] == [{"prs": [1], "with": "main", "files": ["a.txt"]}]
    assert result["needs_fix"] == [{"pr": 1, "depth": 1}]
    assert result["landed"] == []
    assert result["window_commit"] is None


def test_prs_that_share_files_pair_first(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    d = _pr(repo, 4, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    f = _pr(repo, 6, "b.txt", "ALPHA\n")
    e = _pr(repo, 5, "a.txt", "one\ntwo\nthree\nfour\nFIVE\n")

    result = plan(repo, base, [d, f, e])

    assert result["levels"][0] == [[4, 5]]
    assert result["landed"] == [4, 5, 6]
    assert _git(repo, "show", f"{result['window_commit']}:a.txt") == "ONE\ntwo\nthree\nfour\nFIVE"


def test_changed_files_ignore_changelog_fragments(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "frag", "main")
    (repo / "changes").mkdir()
    (repo / "changes" / "1.added.md").write_text("x\n")
    (repo / "b.txt").write_text("beta\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "frag")

    assert changed_files(repo, base, _git(repo, "rev-parse", "HEAD")) == frozenset({"b.txt"})


def test_jaccard() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)


def _check(name: str, state: str) -> dict[str, str]:
    return {"__typename": "StatusContext", "context": name, "state": state}


def _fake_gh_pr_list(monkeypatch: pytest.MonkeyPatch, items: list[dict]) -> list[list[str]]:
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(items))

    monkeypatch.setattr("merge_flow.subprocess.run", run)
    return calls


def test_gh_open_prs_stops_when_the_list_reaches_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"number": number, "isDraft": False, "reviewDecision": "REVIEW_REQUIRED"} for number in range(1, 1001)]
    calls = _fake_gh_pr_list(monkeypatch, items)

    with pytest.raises(SystemExit, match=r"1000.*may be incomplete"):
        _gh_open_prs("owner/repo")

    assert calls[0][calls[0].index("--limit") + 1] == "1000"


def test_gh_open_prs_returns_a_short_list_and_requests_1000(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {"number": 1, "isDraft": False, "reviewDecision": "REVIEW_REQUIRED"},
        {"number": 2, "isDraft": True, "reviewDecision": "APPROVED"},
    ]
    calls = _fake_gh_pr_list(monkeypatch, items)

    assert _gh_open_prs("owner/repo") == items
    assert calls[0][calls[0].index("--limit") + 1] == "1000"


def test_ready_prs_need_approval_green_required_checks_and_no_draft() -> None:
    green = [_check(name, "SUCCESS") for name in ("ci/tests", "ci/standards-check", "ci/security-scan")]
    items = [
        {"number": 10, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": green},
        {"number": 11, "isDraft": True, "reviewDecision": "APPROVED", "statusCheckRollup": green},
        {"number": 12, "isDraft": False, "reviewDecision": "REVIEW_REQUIRED", "statusCheckRollup": green},
        {"number": 13, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": green[:2]},
        {
            "number": 9,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": c["context"], "conclusion": "SUCCESS"} for c in green
            ],
        },
        {
            "number": 14,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [*green[:2], _check("ci/security-scan", "FAILURE")],
        },
    ]

    assert ready_prs(items, ("ci/tests", "ci/standards-check", "ci/security-scan")) == [9, 10]


def test_ready_prs_without_names_require_every_reported_check() -> None:
    items = [
        {"number": 1, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": [_check("a", "SUCCESS")]},
        {
            "number": 2,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [_check("a", "SUCCESS"), _check("b", "FAILURE")],
        },
        {"number": 3, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": []},
    ]

    assert ready_prs(items) == [1]


def test_a_rerun_that_failed_beats_an_earlier_success_with_the_same_name() -> None:
    def run(state: str, at: str) -> dict[str, str]:
        return {"__typename": "CheckRun", "name": "tests", "conclusion": state, "startedAt": at}

    items = [
        {
            "number": 1,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [run("SUCCESS", "2026-11-27T10:00:00Z"), run("FAILURE", "2026-11-27T11:00:00Z")],
        },
        {
            "number": 2,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [run("FAILURE", "2026-11-27T10:00:00Z"), run("SUCCESS", "2026-11-27T11:00:00Z")],
        },
    ]

    assert ready_prs(items, ("tests",)) == [2]


def test_a_rename_still_clashes_with_an_edit_to_the_old_path(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "pr1", "main")
    _git(repo, "mv", "a.txt", "new.txt")
    (repo / "new.txt").write_text("ONE\ntwo\nthree\nfour\nfive\n")
    _git(repo, "commit", "-q", "-am", "rename")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    a = Head(1, sha, changed_files(repo, base, sha))
    b = _pr(repo, 2, "a.txt", "uno\ntwo\nthree\nfour\nfive\n")

    result = plan(repo, base, [a, b])

    assert "a.txt" in a.files
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]
    assert result["held"] == []


def test_github_repo_comes_from_the_remote_url() -> None:
    from merge_flow import github_repo

    assert github_repo("https://github.com/owner/name.git") == "owner/name"
    assert github_repo("https://github.com/owner/name") == "owner/name"
    assert github_repo("git@github.com:owner/name.git") == "owner/name"
    assert github_repo("ssh://git@github.com/owner/name.git") == "owner/name"
    with pytest.raises(ValueError):
        github_repo("https://gitlab.com/owner/name.git")


def test_fetch_writes_only_merge_flow_refs(repo: Path, tmp_path: Path) -> None:
    from merge_flow import fetch

    _git(repo, "tag", "v-probe")
    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")

    fetch(local, str(repo), ["+refs/heads/main:refs/merge-flow/base"])

    assert _git(local, "for-each-ref", "--format=%(refname)") == "refs/merge-flow/base"
    assert not (local / ".git" / "FETCH_HEAD").exists()


def test_a_head_already_on_main_does_not_land(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")

    result = plan(repo, base, [Head(7, base)])

    assert result["landed"] == []
    assert result["already_on_base"] == [7]
    assert result["window_commit"] is None


def test_verify_accepts_the_planned_window_and_rejects_anything_else(repo: Path) -> None:
    from merge_flow import verify

    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "b.txt", "ALPHA\n")
    window = plan(repo, base, [a, c])["window_commit"]

    assert verify(repo, base, {1: a.sha, 3: c.sha}, window) == []

    _git(repo, "checkout", "-q", "--detach", window)
    (repo / "b.txt").write_text("sneaky\n")
    _git(repo, "commit", "-q", "-am", "extra change")
    extra = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    assert verify(repo, base, {1: a.sha, 3: c.sha}, extra) != []

    moved = _pr(repo, 7, "b.txt", "OTHER\n")
    assert verify(repo, base, {1: a.sha, 3: moved.sha}, window) != []


def test_window_body_round_trips_the_landed_heads() -> None:
    from merge_flow import parse_window_body, window_body

    body = window_body("abc123", {1450: "f00", 1458: "ba7"})

    assert "#1450" in body and "#1458" in body
    assert parse_window_body(body) == ("abc123", {1450: "f00", 1458: "ba7"})
    assert parse_window_body("no marker here") is None


def _open_window_call(
    monkeypatch: pytest.MonkeyPatch,
    numbers: list[int],
    issue_data: dict[int, dict] | None = None,
    branch: str = "main",
) -> tuple[str, ...]:
    import merge_flow as cli

    calls: list[tuple[str, ...]] = []

    def gh(*args: str) -> str:
        if args[:2] == ("pr", "view"):
            return json.dumps((issue_data or {}).get(int(args[2]), {"closingIssuesReferences": [], "body": ""}))
        calls.append(args)
        return "https://github.com/o/r/pull/1"

    monkeypatch.setattr(cli, "git", lambda *_args, **_kwargs: (0, ""))
    monkeypatch.setattr(cli, "_gh", gh)
    heads = [cli.Head(number, f"{number:040x}", frozenset()) for number in numbers]
    result = {"landed": numbers, "base": "b" * 40, "window_commit": "a" * 40}

    cli.open_window(Path("."), "origin", "o/r", branch, result, heads)

    return calls[0]


def _open_window_title(monkeypatch: pytest.MonkeyPatch, numbers: list[int]) -> str:
    args = _open_window_call(monkeypatch, numbers)
    return args[args.index("--title") + 1]


def test_open_window_title_uses_the_pr_count_for_a_large_window(monkeypatch: pytest.MonkeyPatch) -> None:
    title = _open_window_title(monkeypatch, list(range(1, 79)))

    assert len(title) <= 256
    assert title == "chore(merge-flow): land 78 PRs into main"


def test_open_window_title_stays_within_limit_for_a_long_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _open_window_call(monkeypatch, list(range(1, 79)), branch="b" * 221)
    title = args[args.index("--title") + 1]

    assert len(title) <= 256
    assert title.startswith("chore(merge-flow): land")


def test_open_window_title_keeps_the_current_title_for_two_prs(monkeypatch: pytest.MonkeyPatch) -> None:
    title = _open_window_title(monkeypatch, [1, 3])

    assert title == "chore(merge-flow): land #1, #3"


def test_open_window_body_lists_distinct_linked_issues_in_ascending_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _open_window_call(
        monkeypatch,
        [2, 1],
        {
            1: {
                "closingIssuesReferences": [{"number": 9}, {"number": 3}],
                "body": "Fixes #12 and refs #9; Closes #1",
            },
            2: {"closingIssuesReferences": [{"number": 12}, {"number": 7}], "body": "RESOLVES #7"},
        },
    )

    body = args[args.index("--body") + 1]
    assert [line for line in body.splitlines() if line.startswith("Refs #")] == [
        "Refs #3",
        "Refs #7",
        "Refs #9",
        "Refs #12",
    ]
    assert "Refs #1" not in [line for line in body.splitlines() if line.startswith("Refs #")]
    assert "Refs #2" not in [line for line in body.splitlines() if line.startswith("Refs #")]


def test_open_window_body_has_no_refs_when_landed_prs_link_no_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _open_window_call(monkeypatch, [1, 2])

    body = args[args.index("--body") + 1]
    assert not any(line.startswith("Refs #") for line in body.splitlines())


def test_land_parses_a_body_created_by_open_window(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    _land_setup(repo, monkeypatch, {}, body_from_open=True)

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 0


def test_open_window_does_not_push_tags_when_follow_tags_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import merge_flow as cli

    remote = tmp_path / "remote.git"
    working = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    working.mkdir()
    _git(working, "init", "-q", "-b", "main")
    _git(working, "config", "user.name", "test")
    _git(working, "config", "user.email", "test@example.com")
    (working / "file.txt").write_text("window\n")
    _git(working, "add", "file.txt")
    _git(working, "commit", "-q", "-m", "window")
    window = _git(working, "rev-parse", "HEAD")
    _git(working, "tag", "-a", "v-unpublished", "-m", "reachable tag", window)
    _git(working, "config", "push.followTags", "true")
    _git(working, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(
        cli,
        "_gh",
        lambda *args: json.dumps({"closingIssuesReferences": [], "body": ""})
        if args[:2] == ("pr", "view")
        else "https://example.invalid/pr/1",
    )

    cli.open_window(
        working,
        "origin",
        "owner/repo",
        "main",
        {"base": "0" * 40, "landed": [1], "window_commit": window},
        [Head(1, window, frozenset({"file.txt"}))],
    )

    refs = subprocess.run(
        ["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert f"refs/heads/merge-flow/window-{window[:12]}" in refs
    assert not any(ref.startswith("refs/tags/") for ref in refs)


def test_green_uses_the_newest_run_and_every_check_by_default() -> None:
    from merge_flow import green

    old_pass = {"__typename": "CheckRun", "name": "t", "conclusion": "SUCCESS", "startedAt": "1"}
    new_fail = {"__typename": "CheckRun", "name": "t", "conclusion": "FAILURE", "startedAt": "2"}

    assert green([old_pass], ())
    assert not green([old_pass, new_fail], ())
    assert not green([], ())
    assert not green([old_pass], ("t", "lint"))


_GREEN = [{"__typename": "CheckRun", "name": "ci", "conclusion": "SUCCESS", "startedAt": "2026-11-27T10:00:00Z"}]


def _land_setup(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    view: dict,
    after_verify: Callable[[dict, list[dict]], None] | None = None,
    body_from_open: bool = False,
) -> list:
    """A planned window for PR 1 with refs/merge-flow/* in place and every GitHub call stubbed."""
    import merge_flow as cli

    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    window = plan(repo, base, [a])["window_commit"]
    for ref, sha in (("base", base), ("pr/1", a.sha), ("window", window)):
        _git(repo, "update-ref", f"refs/merge-flow/{ref}", sha)
    calls: list[tuple[str, ...]] = []
    body = cli.window_body(base, {1: a.sha})
    if body_from_open:
        opened: list[tuple[str, ...]] = []

        def open_gh(*args: str) -> str:
            if args[:2] == ("pr", "view"):
                return json.dumps({"closingIssuesReferences": [], "body": ""})
            opened.append(args)
            return "https://github.com/o/r/pull/42"

        with monkeypatch.context() as opening:
            opening.setattr(cli, "git", lambda *_args, **_kwargs: (0, ""))
            opening.setattr(cli, "_gh", open_gh)
            cli.open_window(
                repo, "origin", "o/r", "main",
                {"base": base, "landed": [1], "window_commit": window}, [a],
            )
        create = opened[0]
        body = create[create.index("--body") + 1]
    pr = {"body": body, "statusCheckRollup": _GREEN, "state": "OPEN"}
    pr |= {"baseRefName": "main", "headRefOid": window} | view

    def gh(*args: str) -> str:
        if args[:2] == ("pr", "view"):
            if args[-2:] == ("--json", "state"):
                calls.append(args)
                return json.dumps({"state": view.get("postMergeState", "MERGED")})
            return json.dumps(pr)
        calls.append(args)
        return ""

    ready = [{"number": 1, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": _GREEN}]
    ready[0] |= {"baseRefName": view.get("sourceBase", "main"), "headRefOid": view.get("sourceHead", a.sha)}
    if "sourceApproved" in view:
        ready[0]["approvedCommits"] = view["sourceApproved"]
    monkeypatch.setattr(cli, "_gh", gh)
    monkeypatch.setattr(cli, "_gh_open_prs", lambda _repo: ready)
    monkeypatch.setattr(cli, "fetch", lambda *_args: None)
    if after_verify is not None:
        verify = cli.verify

        def verify_then_change_state(*args, **kwargs):
            problems = verify(*args, **kwargs)
            after_verify(pr, ready)
            return problems

        monkeypatch.setattr(cli, "verify", verify_then_change_state)
    return calls


def test_land_merges_with_the_checked_head_pinned(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 0
    merge = next(c for c in calls if c[:2] == ("pr", "merge"))
    assert merge[merge.index("--match-head-commit") + 1] == _git(repo, "rev-parse", "refs/merge-flow/window")


def test_land_counts_a_window_merged_before_the_explicit_merge(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import merge_flow as cli

    calls = _land_setup(repo, monkeypatch, {})
    gh = cli._gh

    def bot_merged_first(*args: str) -> str:
        if args[:2] == ("pr", "merge"):
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="Merge already in progress")
        return gh(*args)

    monkeypatch.setattr(cli, "_gh", bot_merged_first)

    assert cli.land_window(repo, "origin", "o/r", "main", 42, ()) == 0
    assert calls[-1][:2] == ("pr", "view")


def test_land_refuses_when_source_readiness_changes_during_local_verification(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from merge_flow import land_window

    def source_turns_red(_window: dict, source_prs: list[dict]) -> None:
        source_prs[0]["reviewDecision"] = "CHANGES_REQUESTED"
        source_prs[0]["statusCheckRollup"] = [_check("ci", "FAILURE")]

    calls = _land_setup(repo, monkeypatch, {}, after_verify=source_turns_red)

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert not any(call[:2] == ("pr", "merge") for call in calls)
    assert "not ready any more: #1" in capsys.readouterr().out


def test_land_refuses_when_window_checks_turn_red_during_local_verification(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from merge_flow import land_window

    def window_checks_turn_red(window: dict, _source_prs: list[dict]) -> None:
        window["statusCheckRollup"] = [_check("ci", "FAILURE")]

    calls = _land_setup(repo, monkeypatch, {}, after_verify=window_checks_turn_red)

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert not any(call[:2] == ("pr", "merge") for call in calls)
    assert "the window's required checks are not all green yet" in capsys.readouterr().out


def test_land_refuses_when_window_closes_during_local_verification(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    def window_closes(window: dict, _source_prs: list[dict]) -> None:
        window["state"] = "CLOSED"

    calls = _land_setup(repo, monkeypatch, {}, after_verify=window_closes)

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert not any(call[:2] == ("pr", "merge") for call in calls)


def test_land_reports_a_queued_window_as_not_landed(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {"postMergeState": "OPEN"})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 2
    output = capsys.readouterr().out
    assert "queued" in output
    assert "not merged yet" in output
    assert "landed" not in output
    assert ("pr", "view", "42", "--repo", "o/r", "--json", "state") in calls


def test_land_refuses_a_window_pr_that_targets_another_branch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {"baseRefName": "release"})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert calls == []


def test_land_refuses_when_the_window_head_moved_after_its_checks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {})
    window = _git(repo, "rev-parse", "refs/merge-flow/window")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", window).split()[1:]
    twin = _git(repo, "commit-tree", f"{window}^{{tree}}", *[x for p in parents for x in ("-p", p)], "-m", "same files")
    _git(repo, "update-ref", "refs/merge-flow/window", twin)

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert calls == []


def test_a_queued_rerun_without_a_start_time_is_not_green() -> None:
    old_pass = {"__typename": "CheckRun", "name": "tests", "conclusion": "SUCCESS", "startedAt": "2026-11-27T10:00:00Z"}
    queued = {"__typename": "CheckRun", "name": "tests", "conclusion": None, "status": "QUEUED", "startedAt": None}
    item = {"number": 42, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": [old_pass, queued]}

    assert ready_prs([item], ("tests",)) == []


def test_ready_prs_only_counts_prs_into_the_base_branch() -> None:
    def item(number: int, base: str) -> dict:
        green = [_check("ci", "SUCCESS")]
        return {"number": number, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": green} | {
            "baseRefName": base
        }

    assert ready_prs([item(1, "main"), item(2, "release")], (), "main") == [1]


def test_land_refuses_a_source_pr_that_targets_another_branch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {"sourceBase": "release"})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert calls == []


def test_verify_rejects_a_window_that_brings_in_an_unlisted_commit(repo: Path) -> None:
    from merge_flow import verify

    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    unlisted = _pr(repo, 9, "c.txt", "not approved\n")
    window = plan(repo, base, [a])["window_commit"]
    smuggled = _git(
        repo, "commit-tree", f"{window}^{{tree}}", "-p", base, "-p", window, "-p", unlisted.sha, "-m", "extra parent"
    )

    assert verify(repo, base, {1: a.sha}, smuggled) != []


def test_verify_rejects_unlisted_merge_history_hidden_by_the_window_tree(repo: Path) -> None:
    from merge_flow import verify

    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "approved a\n")
    b = _pr(repo, 2, "b.txt", "approved b\n")
    heads = {1: a.sha, 2: b.sha}
    expected = plan(repo, base, [a, b])["window_commit"]
    assert expected is not None

    _git(repo, "checkout", "-q", "--detach", a.sha)
    (repo / "rogue.txt").write_text("unapproved history\n")
    _git(repo, "add", "rogue.txt")
    rogue_tree = _git(repo, "write-tree")
    rogue = _git(
        repo,
        "commit-tree",
        rogue_tree,
        "-p",
        base,
        "-p",
        a.sha,
        "-m",
        "unlisted merge",
    )
    forged = _git(
        repo,
        "commit-tree",
        f"{expected}^{{tree}}",
        "-p",
        base,
        "-p",
        rogue,
        "-p",
        a.sha,
        "-p",
        b.sha,
        "-m",
        "window with unlisted merge history",
    )

    problems = verify(repo, base, heads, forged)
    assert problems
    assert any(rogue[:9] in problem for problem in problems)


def test_verify_rejects_an_edited_tree_in_an_intermediate_merge(repo: Path) -> None:
    from merge_flow import verify

    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "approved a\n")
    b = _pr(repo, 2, "b.txt", "approved b\n")
    heads = {1: a.sha, 2: b.sha}
    expected = plan(repo, base, [a, b])["window_commit"]
    assert expected is not None

    _git(repo, "checkout", "-q", "--detach", a.sha)
    (repo / "rogue.txt").write_text("edited intermediate merge\n")
    _git(repo, "add", "rogue.txt")
    edited_tree = _git(repo, "write-tree")
    edited_merge = _git(
        repo,
        "commit-tree",
        edited_tree,
        "-p",
        a.sha,
        "-p",
        b.sha,
        "-m",
        "edited merge tree",
    )
    forged = _git(
        repo,
        "commit-tree",
        f"{expected}^{{tree}}",
        "-p",
        base,
        "-p",
        edited_merge,
        "-m",
        "window with edited intermediate merge",
    )

    problems = verify(repo, base, heads, forged)
    assert problems
    assert any(edited_merge[:9] in problem for problem in problems)


def test_verify_accepts_a_planned_window_for_three_prs(repo: Path) -> None:
    from merge_flow import verify

    base = _git(repo, "rev-parse", "main")
    heads = [
        _pr(repo, 1, "a.txt", "approved a\n"),
        _pr(repo, 2, "b.txt", "approved b\n"),
        _pr(repo, 3, "c.txt", "approved c\n"),
    ]
    window = plan(repo, base, heads)["window_commit"]

    assert window is not None
    assert verify(repo, base, {head.number: head.sha for head in heads}, window) == []


def test_land_refuses_when_a_source_pr_moved_after_the_fetch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {"sourceHead": "f" * 40})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert calls == []


def test_an_approval_counts_only_on_the_head_it_was_given_for() -> None:
    item = {"number": 5, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": [_check("ci", "SUCCESS")]}

    assert ready_prs([item | {"headRefOid": "b", "approvedCommits": ["a"]}]) == []
    assert ready_prs([item | {"headRefOid": "b", "approvedCommits": ["a", "b"]}]) == [5]


def test_land_refuses_a_head_nobody_approved(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from merge_flow import land_window

    calls = _land_setup(repo, monkeypatch, {"sourceApproved": ["0" * 40]})

    assert land_window(repo, "origin", "o/r", "main", 42, ()) == 1
    assert calls == []


def test_the_plan_skips_a_pr_that_moved_after_it_was_listed_ready(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import merge_flow as cli

    base = _git(repo, "rev-parse", "main")
    listed = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    moved = _pr(repo, 2, "b.txt", "BETA\n")
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    item = {"isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": [_check("ci", "SUCCESS")]}
    item |= {"baseRefName": "main"}
    items = [item | {"number": 1, "headRefOid": listed.sha}, item | {"number": 2, "headRefOid": "f" * 40}]

    def fetch(root: Path, _remote: str, _refspecs: list[str]) -> None:
        for ref, sha in (("base", base), ("pr/1", listed.sha), ("pr/2", moved.sha)):
            _git(root, "update-ref", f"refs/merge-flow/{ref}", sha)

    monkeypatch.setattr(cli, "_gh_open_prs", lambda _repo: items)
    monkeypatch.setattr(cli, "fetch", fetch)
    monkeypatch.chdir(repo)
    out = tmp_path / "plan.json"

    assert cli.main(["--json", str(out)]) == 0
    assert json.loads(out.read_text())["prs"] == [1]


def test_github_repo_needs_github_com_as_the_host() -> None:
    from merge_flow import github_repo

    for url in ("https://notgithub.com/o/r.git", "https://github.com.evil.example/o/r", "git@evilgithub.com:o/r"):
        with pytest.raises(ValueError):
            github_repo(url)


@pytest.mark.parametrize("value", ["0", "-3"])
def test_land_refuses_a_pr_number_below_one(value, tmp_path, monkeypatch, capsys):
    # --land 0 used to read as "no --land" and run a plan that exits 0.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as stop:
        cli.main(["--land", value])
    assert stop.value.code == 2
    assert "--land" in capsys.readouterr().err


if __name__ == "__main__":  # CI runs every test file as a script
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
