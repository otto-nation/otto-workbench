#!/usr/bin/env bash
# Carry reviews out of the two layouts that predate the current one: a reviews/
# directory under Claude's home, and a flat file-per-review tree under the
# state root.
#
# Both drains were Python functions at the top of ai/bin/claude-review, run
# unconditionally on every invocation. That is what a migration is for, and
# re-scanning two directories on every review was the cost of not having one.
#
# One file rather than one per layout, because the order is load-bearing: the
# home drain deposits files into the flat tree that the reshape below then has
# to see. Two files would run in filename order, so the coupling would be
# carried by an alphabetical accident that nothing states and no test would
# catch — the reshape would record itself applied, and anything the home drain
# deposited afterwards would keep its flat layout for good.
#
# adoption-sensitive: reshapes <state>/reviews, which is exactly where
# adopt_legacy_workbench_root puts a legacy root's reviews/. Without the marker
# an adoption running after this migration is recorded re-seeds flat reviews
# that nothing reshapes again — and the review system only ever looks for the
# folder layout, so they read as absent rather than as stale.

# _owning_review NAME — the review a flat file belongs to.
#
# For a review's own deliverable that is the stem; for one of the artifacts the
# flat layout parked beside it, the review the artifact is named after.
#
# Matched against a literal list of suffixes, the same reasoning as the
# intermediate patterns below: these name a historical layout, not the current
# convention, so nothing new joins the list. Stripping dotted components
# positionally instead would be unable to tell a review whose own name holds a
# dot (a repo called repo.2fa) from an artifact of a shorter review — the
# string does not carry that boundary, but the closed suffix set does.
#
# The stamped forms are listed separately rather than caught by the plain ones:
# .session.20260101-120000.jsonl does not end in .session.jsonl, so without its
# own case it would fall through to the default and keep the stamp — naming a
# review that never existed, and so never matching the folder it belongs to.
_owning_review() {
  local name="$1"
  case "$name" in
    *.session.[0-9]*.jsonl) echo "${name%.session.*}" ;;
    *.post.[0-9]*.jsonl) echo "${name%.post.*}" ;;
    # The archive stamp is %Y%m%d-%H%M%S. Matched digit by digit rather than as
    # .2*.md so a review of PR 2 in a repo with a dot in its name stays a
    # review.
    *.[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].md) echo "${name%.*.md}" ;;
    *.session.jsonl) echo "${name%.session.jsonl}" ;;
    *.meta.json) echo "${name%.meta.json}" ;;
    *.post.jsonl) echo "${name%.post.jsonl}" ;;
    *.pipeline.json) echo "${name%.pipeline.json}" ;;
    *.prior.md) echo "${name%.prior.md}" ;;
    *.group-*.md | *.group-*.jsonl) echo "${name%.group-*}" ;;
    *.holistic.md) echo "${name%.holistic.md}" ;;
    *.holistic.jsonl) echo "${name%.holistic.jsonl}" ;;
    *.synthesis.jsonl) echo "${name%.synthesis.jsonl}" ;;
    *) echo "${name%.*}" ;;
  esac
}

# _carry_claude_home_reviews REVIEWS — move reviews out of Claude's own home.
#
# Echoes the number of files moved. Anything the state root already holds wins:
# it is the live location, and this source is a layout two refactors old.
#
# The Python this replaces compared against the flat destination only, so a
# home review whose folder already existed was carried in as a flat file that
# the reshape below then skipped for good — stranded where nothing reads it,
# and re-confirmed as stranded on every subsequent invocation. Fixed here
# rather than ported: a one-shot migration gets no second chance to strand
# something differently, and the folder check is the same question the reshape
# already asks.
_carry_claude_home_reviews() {
  local reviews="$1"
  local legacy="$CLAUDE_DIR/reviews"
  local carried=0 f name dest

  [[ -d "$legacy" ]] || { echo 0; return 0; }

  for f in "$legacy"/*.md "$legacy"/*.jsonl; do
    [[ -f "$f" ]] || continue
    name="${f##*/}"
    dest="$reviews/$name"
    [[ -e "$dest" ]] && continue
    [[ -d "$reviews/$(_owning_review "$name")" ]] && continue
    mv "$f" "$dest"
    carried=$(( carried + 1 ))
  done

  echo "$carried"
}

# _is_flat_artifact NAME — true for a .md that belongs to another review.
#
# The flat layout put a review's own artifacts beside it under a dotted prefix,
# so a plain glob for *.md yields prior.md, the per-group findings and the
# timestamped archives alongside the reviews themselves. Reshaping one of those
# as if it were a review of its own produces a directory named after the
# artifact, holding the artifact as its deliverable.
#
# The Python this replaces had the same exposure and avoided it by accident:
# it skipped anything already moved, so the outcome depended on the order
# os.scandir happened to return. A shell glob is sorted, which turns that into
# a reliably wrong answer — "foo.20260101-120000.md" sorts ahead of "foo.md" —
# so the artifacts are excluded by name instead of by luck.
_is_flat_artifact() {
  [[ "$(_owning_review "$1")" != "${1%.md}" ]]
}

# _reshape_one_flat_review REVIEWS DIR_NAME — give one flat review its folder.
_reshape_one_flat_review() {
  local reviews="$1" dir_name="$2"
  local review_dir="$reviews/$dir_name"
  local archive_dir="$review_dir/archives"
  local name

  mkdir -p "$review_dir"
  mv "$reviews/$dir_name.md" "$review_dir/review.md"

  local pair suffix new_name artifact
  for pair in \
    ".session.jsonl:session.jsonl" \
    ".meta.json:meta.json" \
    ".post.jsonl:post.jsonl" \
    ".pipeline.json:pipeline.json" \
    ".prior.md:prior.md"; do
    suffix="${pair%%:*}"
    new_name="${pair##*:}"
    artifact="$reviews/$dir_name$suffix"
    [[ -f "$artifact" ]] || continue
    mv "$artifact" "$review_dir/$new_name"
  done

  # Literal, unlike review_gc's derived list: these name the historical flat
  # layout, not the current convention. A phase added to the enum tomorrow
  # never wrote a file here, so deriving these from Phase would claim
  # otherwise.
  local intermediate
  for intermediate in \
    "$reviews/$dir_name".group-*.md \
    "$reviews/$dir_name".group-*.jsonl \
    "$reviews/$dir_name".holistic.md \
    "$reviews/$dir_name".holistic.jsonl \
    "$reviews/$dir_name".synthesis.jsonl; do
    [[ -f "$intermediate" ]] || continue
    name="${intermediate##*/}"
    mv "$intermediate" "$review_dir/${name#"$dir_name".}"
  done

  # Archives are the one group that cannot be matched by a fixed suffix: the
  # stamp sits in the middle of the name. Rather than spell the stamp out a
  # second time here — where a .2*.md shorthand would take a sibling review
  # whose name merely begins with a 2 after the dot, burying repo.2fa-42.md
  # under repo/archives/2fa-42.md — the candidates are filtered through
  # _owning_review, so both sides share one definition of what a stamp is.
  local candidate rest
  for candidate in "$reviews/$dir_name."*.md "$reviews/$dir_name."*.jsonl; do
    [[ -f "$candidate" ]] || continue
    name="${candidate##*/}"
    [[ "$(_owning_review "$name")" == "$dir_name" ]] || continue
    rest="${name#"$dir_name".}"
    # Everything with a fixed suffix has already been moved above, so what is
    # left under this review's name is stamped by elimination.
    mkdir -p "$archive_dir"
    mv "$candidate" "$archive_dir/$rest"
  done
}

# _reshape_flat_reviews REVIEWS — echo how many flat reviews got a folder.
_reshape_flat_reviews() {
  local reviews="$1"
  local reshaped=0 flat name dir_name

  for flat in "$reviews"/*.md; do
    [[ -f "$flat" ]] || continue
    name="${flat##*/}"
    _is_flat_artifact "$name" && continue
    dir_name="${name%.md}"
    [[ -d "$reviews/$dir_name" ]] && continue
    _reshape_one_flat_review "$reviews" "$dir_name"
    reshaped=$(( reshaped + 1 ))
  done

  echo "$reshaped"
}

migration_20260915_review_folder_layout() {
  local reviews="$REVIEWS_DIR"
  local carried reshaped

  # Not MIGRATION_DEFERRED: a machine with no reviews root has never run a
  # review, and the only writer of either legacy layout was retired two
  # refactors ago. There is nothing for a later sync to find, so deferring
  # would re-run this on every sync forever.
  [[ -d "$reviews" || -d "$CLAUDE_DIR/reviews" ]] || return "$MIGRATION_NOOP"

  mkdir -p "$reviews"
  carried="$(_carry_claude_home_reviews "$reviews")"
  reshaped="$(_reshape_flat_reviews "$reviews")"
  rmdir "$CLAUDE_DIR/reviews" 2> /dev/null || true

  (( carried == 0 && reshaped == 0 )) && return "$MIGRATION_NOOP"

  success "Carried $carried review file(s) out of $CLAUDE_DIR/reviews; gave $reshaped review(s) the folder layout"
  return 0
}
