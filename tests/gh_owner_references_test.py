"""gh.owner_references: which owners published text names."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.github_owners
import gh.owner_references
import gh.owners

Kind = gh.owner_references.ReferenceKind


def _map(*specs: str) -> gh.owners.OwnerMap:
    return gh.owners.OwnerMap(config.github_owners.owner_index({"work": list(specs)}))


def _owner(spec: str) -> config.github_owners.Owner:
    return config.github_owners.parse_owner(spec)


def _ref(kind: Kind, spec: str, text: str) -> gh.owner_references.Reference:
    return gh.owner_references.Reference(kind, _owner(spec), text)


def _refs(text: str, *specs: str) -> tuple[gh.owner_references.Reference, ...]:
    return gh.owner_references.references(text, _map(*specs))


def test_each_form_is_a_reference():
    text = (
        "otto-nation/lib#12 and otto-nation/lib@deadbee "
        "https://github.com/otto-nation/lib @otto-nation"
    )
    assert _refs(text, "otto-nation") == (
        _ref(Kind.REPO_ISSUE, "otto-nation", "otto-nation/lib#12"),
        _ref(Kind.REPO_COMMIT, "otto-nation", "otto-nation/lib@deadbee"),
        _ref(Kind.URL, "otto-nation", "https://github.com/otto-nation/lib"),
        _ref(Kind.MENTION, "otto-nation", "@otto-nation"),
    )


def test_a_ghe_owner_is_not_the_github_com_login():
    owners = _map("ghe.corp/foo")
    ghe = "https://ghe.corp/foo/bar"
    assert gh.owner_references.references(ghe, owners) == (
        _ref(Kind.URL, "ghe.corp/foo", ghe),
    )
    assert gh.owner_references.references("foo/bar#12", owners) == ()


def test_matching_is_case_insensitive():
    text = "OTTO-NATION/Lib#12 HTTPS://GitHub.COM/Otto-Nation/x @Otto-Nation"
    refs = _refs(text, "otto-nation")
    assert tuple(r.owner for r in refs) == (_owner("otto-nation"),) * 3
    assert [r.kind for r in refs] == [Kind.REPO_ISSUE, Kind.URL, Kind.MENTION]


def test_an_email_is_not_a_mention():
    assert _refs("a@otto-nation.com and x@acme.io", "otto-nation", "acme") == ()


def test_a_bare_issue_number_is_not_a_reference():
    assert _refs("see #12", "otto-nation") == ()


def test_a_six_hex_sha_is_not_a_commit_reference():
    assert _refs("otto-nation/lib@deadbe", "otto-nation") == ()


def test_a_forty_one_hex_sha_is_not_a_commit_reference():
    assert _refs("otto-nation/lib@" + "a" * 41, "otto-nation") == ()


def test_a_seven_and_forty_hex_sha_is_a_commit_reference():
    seven = "otto-nation/lib@deadbee"
    forty = "otto-nation/lib@" + "a" * 40
    assert _refs(seven, "otto-nation") == (_ref(Kind.REPO_COMMIT, "otto-nation", seven),)
    assert _refs(forty, "otto-nation") == (_ref(Kind.REPO_COMMIT, "otto-nation", forty),)


def test_decorator_names_are_not_mentions():
    refs = _refs("@property and @dataclass then @otto-nation", "otto-nation")
    assert refs == (_ref(Kind.MENTION, "otto-nation", "@otto-nation"),)


def test_a_url_on_an_unconfigured_host_is_ignored():
    assert _refs("https://gitlab.example/otto-nation/lib", "otto-nation") == ()
    assert _refs("https://ghe.other/foo/bar", "ghe.corp/foo") == ()


def test_an_upstream_owner_is_not_returned():
    refs = _refs("stranger/lib#1 and otto-nation/lib#1", "otto-nation")
    assert refs == (_ref(Kind.REPO_ISSUE, "otto-nation", "otto-nation/lib#1"),)


def test_a_url_is_not_also_an_issue_reference():
    url = "https://github.com/otto-nation/lib#12"
    assert _refs(url, "otto-nation") == (_ref(Kind.URL, "otto-nation", url),)


def test_duplicates_keep_the_first_in_text_order():
    refs = _refs("otto-nation/lib#12 then otto-nation/lib#12", "otto-nation")
    assert refs == (_ref(Kind.REPO_ISSUE, "otto-nation", "otto-nation/lib#12"),)


def test_a_team_mention_names_the_org():
    refs = _refs("ask @otto-nation/core", "otto-nation")
    assert refs == (_ref(Kind.MENTION, "otto-nation", "@otto-nation/core"),)
