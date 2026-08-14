"""Tests for the run-target path contract.

SLUG_VECTORS and REPO_KEY_VECTORS are cross-repo fixtures: ui-code's TypeScript
mirror asserts against the same tables. Changing a row changes where live runs
look for their own state, in a repo whose tests cannot see this one.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr_target


# Cross-repo fixture. ui-code reimplements slug() in TypeScript and asserts
# against this same table; nothing in this repo's CI can see that side, so a row
# edited here drifts silently until a live run looks for state in a directory the
# other implementation never writes. Change a row only together with ui-code.
SLUG_VECTORS = [
    ("main", "main"),
    ("isaac/fix/target_scoped_run_lock", "isaac-fix-target_scoped_run_lock"),
    ("feat/a--b", "feat-a--b"),
    ("release/v1.2.3", "release-v1.2.3"),
    ("/leading/", "leading"),
    ("dependabot/npm_and_yarn/foo/bar-1.0.0", "dependabot-npm_and_yarn-foo-bar-1.0.0"),
]


@pytest.mark.parametrize("branch,expected", SLUG_VECTORS)
def test_slug_vectors(branch, expected):
    assert pr_target.slug(branch) == expected


def test_target_dir_is_rooted_at_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    assert pr_target.target_dir("otto-workbench-3df215bb", "feat/x") == (
        tmp_path / "pr" / "otto-workbench-3df215bb-feat-x"
    )


def test_target_dir_follows_a_moved_state_root(tmp_path, monkeypatch):
    """state_dir() resolves per call, so #624 phase 4 carries pr/ for free."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "old"))
    before = pr_target.target_dir("repo", "main")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "new"))
    after = pr_target.target_dir("repo", "main")
    assert before != after
    assert after.parent.parent == tmp_path / "new"


# The URL-to-key contract, one row per form git accepts as an origin. These are
# the assertions, not illustrations of them: a changed row is a changed state
# directory, and any run already holding the old one keeps holding it.
#
# Every key is <readable>-<digest>, where the digest is the first 8 hex of the
# SHA-256 of the canonical form and is what makes the key injective. The
# readable part is only there so a human can tell two directories apart, so it
# is allowed to be lossy; the rows below that share a readable part and differ
# only in the digest are the point of the design, not an accident of it.
REPO_KEY_VECTORS = [
    # canonical "acme/widget" — every spelling of one repo takes one key.
    ("git@github.com:acme/widget.git", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget.git", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget/", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget.git/", "acme-widget-b9d71e86"),
    ("https://github.com/acme//widget.git", "acme-widget-b9d71e86"),
    # `git clone .../repo/.git` is a spelling git accepts. The suffix strip
    # uncovers a trailing slash, so slashes are normalized on both sides of it.
    ("https://github.com/acme/widget/.git", "acme-widget-b9d71e86"),
    ("ssh://git@github.com/acme/widget.git", "acme-widget-b9d71e86"),
    ("ssh://git@host:2222/acme/widget.git", "acme-widget-b9d71e86"),
    ("git://host/acme/widget.git", "acme-widget-b9d71e86"),
    ("https://user:token@host/acme/widget", "acme-widget-b9d71e86"),
    # file:// names no host whatever authority it carries: git ignores a file
    # URL's authority and clones the path, so all three of these are one target.
    ("file:///srv/git/widget.git", "widget-8ac140ce"),
    ("file://localhost/srv/git/widget.git", "widget-8ac140ce"),
    ("file://bogushost/srv/git/widget.git", "widget-8ac140ce"),
    # An ~/.ssh/config Host alias. Scp-style with no "user@", which git accepts
    # and which therefore has to be qualified like any other hosted remote.
    ("gitbox:acme/widget.git", "acme-widget-b9d71e86"),
    ("git@host:widget.git", "widget-8ac140ce"),
    # The whole path below the host, not its last two segments — group-a/platform/api
    # and group-b/platform/api are different repos.
    ("https://gitlab.com/group/subgroup/widget.git", "group-subgroup-widget-2cc27ab1"),
    ("https://gitlab.com/group/sub/widget.git", "group-sub-widget-5fe8bb83"),
    ("https://gitlab.com/group/sub/team/service/widget.git",
     "group-sub-team-service-widget-b37e4eef"),
    # A local remote keys on its trailing segment alone — see the ceiling in
    # pr_target: leading directories are machine-specific.
    ("/srv/git/widget.git", "widget-8ac140ce"),
    ("/srv/git/widget/.git", "widget-8ac140ce"),
    ("/srv/mirrors/otto-workbench/", "otto-workbench-3df215bb"),
    ("../widget", "widget-8ac140ce"),
    # A remote naming no path names no repo. Keying the userinfo, the host, or a
    # file URL's discarded authority would give every such URL one directory.
    ("ssh://git@github.com/", None),
    ("https://github.com", None),
    ("git@github.com:", None),
    ("file://localhost/", None),
    ("file://bogushost/", None),
    ("file:///", None),
    ("/", None),
    ("", None),
    # The canonical form folds A-Z to a-z and drops one trailing ".git" through
    # that same fold: GitHub and GitLab treat a repo path case-insensitively, so
    # every casing of one path is one repo and takes one lock.
    ("https://github.com/Acme/Widget.git", "acme-widget-b9d71e86"),
    ("git@github.com:acme/widget.GIT", "acme-widget-b9d71e86"),
    ("https://github.com/Acme/Widget.GIT", "acme-widget-b9d71e86"),
    ("https://github.com/acme/API", "acme-api-c7198fbc"),
    # A-Z and nothing else. É (U+00C9) is left as it is, so the mirror never has
    # to agree with this runtime about a Unicode version or a locale. The price
    # is these two rows: a mixed-case non-ASCII path is two keys, not one.
    ("https://github.com/acme/CAFÉ", "acme-caf-bd08d87c"),
    ("https://github.com/acme/café", "acme-caf-84687fb6"),
    # Segments that slug to nothing contribute nothing readable; the digest is
    # what keeps them apart. Without it every such repo under acme/ shares a key.
    ("https://github.com/acme/文档.git", "acme-3aa38a61"),
    ("https://github.com/acme/日本語.git", "acme-bc6e6e54"),
    ("https://github.com/acme/@@@.git", "acme-cbe08fd8"),
    # Nothing readable at all: the key is the digest alone.
    ("https://github.com/文档", "2687ccdb"),
    # A lossy slug, not an empty one. The readable part collides deliberately.
    ("https://github.com/acme/wídget.git", "acme-w-dget-7aa6a4f7"),
    ("https://github.com/acme/wîdget.git", "acme-w-dget-7fb0703f"),
    # The slug's separator is legal inside a segment, and its strip("-") eats an
    # edge hyphen. Both readable parts collide; both keys do not.
    ("https://github.com/acme/-widget.git", "acme--widget-c023fff7"),
    ("https://github.com/acme/wid/get.git", "acme-wid-get-7ba0b50c"),
    ("https://github.com/acme/wid-get.git", "acme-wid-get-273cf4a6"),
    # Relative components survive as readable text, never as a path component:
    # the digest suffix means no key can be "." or "..".
    ("https://github.com/..", "..-5ec1f7e7"),
    ("https://github.com/./widget.git", ".-widget-57d146cd"),
]


@pytest.mark.parametrize("url,expected", REPO_KEY_VECTORS)
def test_repo_key_vectors(url, expected):
    assert pr_target._repo_key(url) == expected


@pytest.mark.parametrize("a,b", [
    ("git@github.com:acme/api.git", "git@github.com:other-org/api.git"),
    ("gitbox:acme/api.git", "gitbox:other-org/api.git"),
    ("https://gitlab.com/group-a/platform/api.git",
     "https://gitlab.com/group-b/platform/api.git"),
])
def test_the_namespace_is_what_keeps_same_named_repos_apart(a, b):
    """The whole point of qualifying the key — one shared dir is one shared lock."""
    assert pr_target._repo_key(a) != pr_target._repo_key(b)


def test_a_file_url_keys_the_same_as_the_path_it_names():
    """One remote spelled two ways is still one target, so still one run.lock."""
    assert pr_target._repo_key("file:///srv/git/widget.git") == \
        pr_target._repo_key("/srv/git/widget.git")


@pytest.mark.parametrize("authority", ["", "localhost", "bogushost"])
def test_a_file_url_ignores_its_authority_exactly_as_git_does(authority):
    """`git clone file://bogushost/srv/git/w.git` clones /srv/git/w.git.

    The authority is recorded verbatim in remote.origin.url, so keying on it
    would give one clone as many state dirs as it has spellings.
    """
    assert pr_target._repo_key(f"file://{authority}/srv/git/widget.git") == \
        "widget-8ac140ce"


@pytest.mark.parametrize("a,b", [
    # Round 3's regression: the whole-path pass kept the edge hyphen that the
    # per-segment strip("-") ate, so these two slugged alike.
    ("https://github.com/acme/-widget.git", "https://github.com/acme/widget.git"),
    # Round 2's, never reported: "-" is legal inside a segment, so joining
    # segments with it cannot tell a separator from content.
    ("https://github.com/acme/wid/get.git", "https://github.com/acme/wid-get.git"),
    # Round 3 declared this one an accepted ceiling. The digest closes it.
    ("https://github.com/acme/wídget.git", "https://github.com/acme/wîdget.git"),
    # Two segments that both slug to nothing.
    ("https://github.com/acme/文档.git", "https://github.com/acme/日本語.git"),
])
def test_paths_that_flatten_alike_still_key_apart(a, b):
    """Two unrelated repos, or one state.json and one run.lock shared silently.

    Each pair flattens to one readable string. The digest is the whole reason
    they are not one directory.
    """
    assert pr_target._repo_key(a) != pr_target._repo_key(b)


def test_the_digest_suffix_is_stable_across_calls():
    """A key that moves between calls is a run that loses track of its own state.

    The literal is the assertion: recomputing the hash here would only prove the
    test can call hashlib.
    """
    key = pr_target._repo_key("https://github.com/acme/文档.git")
    assert key == "acme-3aa38a61"
    assert pr_target._repo_key("https://github.com/acme/文档.git") == key
    other = pr_target._repo_key("https://github.com/acme/日本語.git")
    assert other == "acme-bc6e6e54"
    assert pr_target._repo_key("https://github.com/acme/日本語.git") == other


def test_the_repo_key_folds_case_but_the_branch_slug_does_not():
    """The asymmetry is deliberate: repo paths are case-insensitive, refs are not.

    `feat/A` and `feat/a` are two branches on every git host, so folding the
    branch slug would point two live runs at one lock.
    """
    assert pr_target._repo_key("https://github.com/Acme/Widget.git") == \
        pr_target._repo_key("https://github.com/acme/widget.git")
    assert pr_target.target_dir("acme-widget-b9d71e86", "feat/A") != \
        pr_target.target_dir("acme-widget-b9d71e86", "feat/a")


def test_the_git_suffix_strip_ignores_case():
    """`.GIT` and `.git` name one repo, so a clone spelled either way is one target."""
    assert pr_target._repo_key("git@github.com:acme/widget.GIT") == \
        pr_target._repo_key("git@github.com:acme/widget.git")


@pytest.mark.parametrize("with_dot_git,plain", [
    ("https://github.com/acme/widget/.git", "https://github.com/acme/widget"),
    ("/srv/git/widget/.git", "/srv/git/widget"),
    ("file:///srv/git/widget/.git", "file:///srv/git/widget"),
])
def test_a_dot_git_directory_keys_as_the_repo_holding_it(with_dot_git, plain):
    """`git clone /path/to/repo/.git` is a spelling git accepts.

    Stripping the suffix uncovers a trailing slash, so a slash pass that ran
    only before the strip left one: the hosted spelling took a second directory
    and a second lock, and the local spelling canonicalized to "" and reported
    no state at all for a repo that has some.
    """
    assert pr_target._repo_key(with_dot_git) == pr_target._repo_key(plain)
    assert pr_target._repo_key(with_dot_git) is not None


def test_the_case_fold_maps_a_to_z_and_nothing_else():
    """The fold is what the digest hashes, so it cannot depend on the runtime.

    `.toLocaleLowerCase()` folds ASCII `I` to `ı` under a Turkish locale, and a
    Unicode-wide fold moves with the runtime's Unicode version — either one
    hands one repo two keys depending on where the process runs. Folding only
    U+0041-U+005A removes both channels, at the cost of the last two assertions.
    """
    assert pr_target._canonical("https://github.com/ACME/API") == "acme/api"
    assert pr_target._repo_key("https://github.com/acme/API") == "acme-api-c7198fbc"
    # É (U+00C9) is cased, and is deliberately left alone.
    assert pr_target._canonical("https://github.com/acme/CAFÉ") == "acme/cafÉ"
    assert pr_target._repo_key("https://github.com/acme/CAFÉ") != \
        pr_target._repo_key("https://github.com/acme/café")


@pytest.mark.parametrize("url", [u for u, key in REPO_KEY_VECTORS if key])
def test_a_key_is_always_one_safe_path_component(url, tmp_path, monkeypatch):
    """A key holding "/", "." or ".." would put a run's state outside pr/."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    key = pr_target._repo_key(url)
    assert "/" not in key
    assert key not in (".", "..")
    assert pr_target.target_dir(key, "main").resolve().parent == (tmp_path / "pr")


@pytest.mark.parametrize("url", ["https://github.com/..",
                                 "https://github.com/./widget.git"])
def test_relative_components_survive_only_as_readable_text(url):
    """The digest suffix is what makes ".." unable to be a whole component."""
    assert "-" in pr_target._repo_key(url)


_LONG = "https://github.com/acme/" + "x" * 70


def test_the_readable_part_is_capped_and_the_digest_carries_the_rest():
    """Two paths differing only past the cap are still two targets.

    The cap is the reason they share a readable prefix, and the digest is the
    reason that sharing costs nothing.
    """
    one = pr_target._repo_key(f"{_LONG}/one.git")
    two = pr_target._repo_key(f"{_LONG}/two.git")
    readable_one, _, digest_one = one.rpartition("-")
    readable_two, _, digest_two = two.rpartition("-")
    assert len(readable_one) == 64
    assert readable_one == readable_two
    assert digest_one != digest_two
    assert one != two


def test_a_deeply_nested_path_stays_whole_under_the_cap():
    """The cap is for pathological paths; ordinary GitLab depth never reaches it."""
    key = pr_target._repo_key("https://gitlab.com/group/sub/team/service/widget.git")
    readable = key.rpartition("-")[0]
    assert readable == "group-sub-team-service-widget"
    assert len(readable) < 64


def test_the_readable_part_never_ends_in_a_dash():
    """Truncation lands mid-slug, and "acme--<digest>" reads like an empty segment.

    The 65-character slug is cut to 64, which lands on the separator; the literal
    is the assertion because the pre-truncation slug also ends in "-z".
    """
    key = pr_target._repo_key("https://github.com/" + "y" * 63 + "/z")
    assert key == "y" * 63 + "-72a8ee35"


def _git_repo(path: Path, origin: str, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


@pytest.mark.parametrize("origin,expected", [
    ("git@github.com:acme/widget.git", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget.git", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget", "acme-widget-b9d71e86"),
    ("https://github.com/acme/widget/", "acme-widget-b9d71e86"),
    ("gitbox:acme/widget.git", "acme-widget-b9d71e86"),
    ("file:///srv/git/widget.git", "widget-8ac140ce"),
    ("https://gitlab.com/group/subgroup/widget.git", "group-subgroup-widget-2cc27ab1"),
    ("/srv/git/widget.git", "widget-8ac140ce"),
])
def test_repo_key_from_origin_reads_the_remote(tmp_path, origin, expected):
    assert pr_target.repo_key_from_origin(str(_git_repo(tmp_path / "wt", origin))) == expected


def test_repo_key_from_origin_is_none_without_an_origin(tmp_path):
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    assert pr_target.repo_key_from_origin(str(path)) is None


def test_target_dir_for_checkout_matches_target_dir(tmp_path, monkeypatch):
    """The two derivations of one identity, asserted equal rather than assumed."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git", "feat/login")
    assert pr_target.target_dir_for_checkout(wt) == pr_target.target_dir(
        "acme-widget-b9d71e86", "feat/login")


def test_target_dir_for_checkout_prefers_origin_over_any_api_name(tmp_path, monkeypatch):
    """The derived directory name comes from `origin`, not any API-reported name.

    No `gh` call exists in pr_target at all, so there is no code path here that
    a network name could reach — asserted by the module's contents, not by
    breaking PATH to prove git can't shell out.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "https://github.com/acme/renamed-clone.git", "main")
    assert pr_target.target_dir_for_checkout(wt).name == \
        "acme-renamed-clone-2027bcd9-main"


def test_target_dir_for_checkout_is_none_without_an_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    assert pr_target.target_dir_for_checkout(path) is None


def test_target_dir_for_checkout_is_none_on_detached_head(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git")
    # Layered onto os.environ, not substituted for it: a replacement env has to
    # guess PATH, and git is not under /usr/bin on a Homebrew install.
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env={**os.environ,
                                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    subprocess.run(["git", "-C", str(wt), "checkout", "-q", "--detach", "HEAD"], check=True)
    assert pr_target.target_dir_for_checkout(wt) is None
