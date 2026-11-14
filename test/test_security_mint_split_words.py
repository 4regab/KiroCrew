"""A mint behind a shell word the tokenizer splits inside its own substitution.

Two spellings hide a mint whose verb arrives through a substitution:

* a leading assignment word whose substitution holds a space
  (``X=$(echo hi) <product> "$(printf token)"``): the tokens split it into
  ``x=$(echo`` and ``hi)``, so ``hi)`` reads as the program;
* a double-quoted assignment value that mixes a parameter expansion holding a quote
  with a command substitution (``T="${U:+"'"}$(printf token)"; <product> $T``): the
  substitution's span, read from the start of the word, lets the quote in the
  ``${...}`` prefix pair across it.

The floor also reads each frame with those words rejoined, reads a substitution's
span from its own opener, and places each assignment by its ``NAME=`` when the raw word
count does not pair. Every row in ``_MINTS`` is allowed with that removed and refused
with it; the controls stay allowed.
"""

from __future__ import annotations

import pytest

from kiro_crew import security

_N = "kiro" + "crew"
_Q = "'"

_MINTS = [
    f'X=$(echo hi) {_N} "$(printf token)"',
    f'X=$(echo hi) Y=1 {_N} "$(printf token)"',
    f'env X=$(echo a b) {_N} "$(printf token)"',
    f'T="${{U:+"{_Q}"}}$(printf token)"; {_N} $T',
    f'T="${{U:-"{_Q}"}}$(printf token)"; {_N} $T',
    f'export T="${{U:+"{_Q}"}}$(printf token)"; {_N} $T',
    f'T="${{U:+"{_Q}"}}`printf token`"; {_N} $T',
]

# Refused by the as-tokenized reading too; pinned so they stay refused.
_STILL_REFUSED = [
    f'X=`echo hi` {_N} "$(printf token)"',
    f"X=$(echo hi) {_N} $(printf token)",
    f'T="${{U:+x}}$(printf token)"; {_N} $T',
]

_CONTROLS = [
    f"X=$(echo hi) {_N} doctor",
    f'T="${{U:+"{_Q}"}}$(printf doctor)"; {_N} $T',
    'X=$(echo hi) echo "$(printf token)"',
    f'T="${{U:+"{_Q}"}}$(printf token)"; echo $T',
    "X=$(echo hi) ls -la",
]


@pytest.mark.parametrize("cmd", _MINTS + _STILL_REFUSED)
def test_a_mint_behind_a_split_word_is_refused(cmd: str) -> None:
    assert security.is_denied(cmd), f"bash runs the mint: {cmd!r}"


@pytest.mark.parametrize("cmd", _CONTROLS)
def test_the_same_shapes_without_a_mint_stay_allowed(cmd: str) -> None:
    assert not security.is_denied(cmd), cmd


def test_an_opener_that_never_closes_is_read_in_linear_work() -> None:
    # A literal ``$(`` before many words (``grep -F '$(' <paths>``) never closes. Each
    # token is scanned once, so doubling the words about doubles the work.
    from denied_commands_security_helpers import _interpreter_work

    from kiro_crew.security.shell_normalizer_walk import _rejoin_split_substitutions

    def work(n: int) -> int:
        tokens = ["grep", "-f", "$("] + [f"src/p{i}.py" for i in range(n)]
        return _interpreter_work(lambda: _rejoin_split_substitutions(tokens))

    assert work(800) < 3 * work(400)


_BASE = f'X=$(echo hi) {_N} "$(printf token)"'

# A later opener that never closes (one token) keeps every run rejoined before it.
_TAIL_MINTS = [
    f"{_BASE}; echo '$('",
    f"{_BASE} '$('",
    f"{_BASE} '`'",
    f"{_BASE} # $(",
    f"{_BASE} && grep -F '$(' f",
]

_TAIL_CONTROLS = [
    "grep -F '$(' src/a.py src/b.py",
    f"X=$(echo hi) {_N} status",
    "echo '`' done",
    "ls # $( not closed",
]


@pytest.mark.parametrize("cmd", _TAIL_MINTS)
def test_a_never_closing_tail_keeps_the_rejoined_mint_refused(cmd: str) -> None:
    assert security.is_denied(cmd), f"bash runs the mint: {cmd!r}"


@pytest.mark.parametrize("cmd", _TAIL_CONTROLS)
def test_a_never_closing_tail_alone_stays_allowed(cmd: str) -> None:
    assert not security.is_denied(cmd), cmd


# The quoted-substitution mint behind one more decoration: a prefix assignment before a
# ``bash -c`` use, a ``{ }`` or ``( )`` wrapper, or a no-op naming the product in the body.
_DECORATED_MINTS = [
    f'X=$(echo hi) T=$(: "{_Q}"; echo token); bash -c "{_N} $T"',
    f'{{ T=$(: "{_Q}"; echo token); {_N} $T; }}',
    f'{{ T=$(: "{_Q}"; echo token); }}; {_N} $T',
    f'( T=$(: "{_Q}"; echo token); {_N} $T )',
    f'T=$(: "{_Q}"; echo token; : {_N}); {_N} $T',
    f'if true; then ( T=$(: "{_Q}"; echo token); {_N} $T ); fi',
    f'if true; then {{ T=$(: "{_Q}"; echo token); {_N} $T; }}; fi',
    f'while :; do ( T=$(: "{_Q}"; echo token); {_N} $T ); break; done',
    f'! ( T=$(: "{_Q}"; echo token); {_N} $T )',
    f'! {{ T=$(: "{_Q}"; echo token); {_N} $T; }}',
    f'time ( T=$(: "{_Q}"; echo token); {_N} $T )',
    f'{{ ( T=$(: "{_Q}"; echo token); {_N} $T ); }}',
    f'if ( T=$(: "{_Q}"; echo token); {_N} $T ); then :; fi',
    f'while ( T=$(: "{_Q}"; echo token); {_N} $T ); do break; done',
    f'until ( T=$(: "{_Q}"; echo token); {_N} $T ); do :; done',
    f'if {{ T=$(: "{_Q}"; echo token); {_N} $T; }}; then :; fi',
    f'if {{ T=$(: "{_Q}"; echo token); }}; then {_N} $T; fi',
    f'while {{ T=$(: "{_Q}"; echo token); }}; do {_N} $T; break; done',
    f'case x in (x) ( T=$(: "{_Q}"; echo token); {_N} $T );; esac',
    f'case x in (x) {{ T=$(: "{_Q}"; echo token); {_N} $T; }};; esac',
    f'time -p ( T=$(: "{_Q}"; echo token); {_N} $T )',
]

# What bash does not run as a mint: a subshell's binding ends at its ``)``, an uncalled
# function body runs nothing, and a body naming only the program binds the program.
_DECORATED_CONTROLS = [
    f'( T=$(: "{_Q}"; echo token) ); {_N} $T',
    f'f(){{ T=$(: "{_Q}"; echo token); }}; {_N} $T',
    f'function f {{ T=$(: "{_Q}"; echo token); }}; {_N} $T',
    f"X=$(echo hi) {_N} status",
    f'T=$(: "{_Q}"; : {_N}); echo $T',
    f"(cd /tmp && {_N} status)",
    f"if true; then ( {_N} status ); fi",
    f"if ( {_N} status ); then :; fi",
    "while ( false ); do :; done",
]


@pytest.mark.parametrize("cmd", _DECORATED_MINTS)
def test_a_decorated_quoted_substitution_mint_is_refused(cmd: str) -> None:
    assert security.is_denied(cmd), f"bash runs the mint: {cmd!r}"


@pytest.mark.parametrize("cmd", _DECORATED_CONTROLS)
def test_the_decorations_without_a_mint_stay_allowed(cmd: str) -> None:
    assert not security.is_denied(cmd), cmd


def test_a_deep_subshell_nesting_is_read_in_linear_work() -> None:
    # Each subshell body is found in one pass and only the innermost one holding an
    # assignment is walked, so doubling the nesting about doubles the work.
    from denied_commands_security_helpers import _interpreter_work

    def work(n: int) -> int:
        cmd = f'T=$(: "{_Q}"; echo token); ' + "( " * n + f"{_N} $T" + " )" * n
        return _interpreter_work(lambda: security.is_denied(cmd))

    assert work(160) < 3 * work(80)


def test_a_deep_subshell_nesting_does_not_rescan_its_text(monkeypatch) -> None:
    # A regex rescan runs in C, which ``_interpreter_work`` cannot see: count the
    # characters the assignment pattern is asked to read instead.
    from kiro_crew.security import shell_normalizer_walk as walk

    def scanned(n: int) -> int:
        cmd = f'T=$(: "{_Q}"; echo token); ' + "( " * n + f"{_N} $T" + " )" * n
        real = walk._SUBST_ASSIGN_RE
        read = [0]

        class Counting:
            def finditer(self, text, *a):
                read[0] += len(text)
                return real.finditer(text, *a)

            def search(self, text, *a):
                read[0] += len(text)
                return real.search(text, *a)

        with monkeypatch.context() as scoped:
            scoped.setattr(walk, "_SUBST_ASSIGN_RE", Counting())
            walk._subshell_bodies(cmd)
        return read[0]

    assert scanned(800) < 3 * scanned(400)


def test_the_substitution_reader_is_memoised_across_the_walk() -> None:
    # The payload walk reads the same command through many frames (suffixes), so without
    # a per-gate-call memo _raw_substitution_mint_name would re-read each assignment's
    # body once per frame. @once_per_call collapses that: every distinct value string is
    # computed at most once per is_denied call, so the call count equals the distinct
    # count. (An unproven close still reads the whole remainder -- the memo removes the
    # per-frame factor, not the per-assignment one; the residual cost is the baseline
    # resolver's own whole-remainder read, tracked separately.)
    from kiro_crew.security import shell_normalizer as sn

    inner = sn._raw_substitution_mint_name.__wrapped__
    calls: list[str] = []

    def spy(value: str) -> object:
        calls.append(value)
        return inner(value)

    cmd = "a=$(: #); " * 40 + f"{_N} doctor"
    with pytest.MonkeyPatch.context() as scoped:
        from kiro_crew.security import call_views

        scoped.setattr(sn, "_raw_substitution_mint_name", call_views.once_per_call()(spy))
        security.is_denied(cmd)

    assert calls, "the reader was not exercised"
    assert len(calls) == len(set(calls)), (len(calls), len(set(calls)))


# GPT 6.1 on 899c268a: two bypasses of this change's own coverage, each a mint bash runs,
# allowed on origin/main and on that head, refused here.
_NESTED_SCOPE_MINTS = [
    # F1: a child subshell's assignment must not suppress the enclosing subshell's own.
    f'( ( X=$(echo hi) ); T=$(: "{_Q}"; echo token); {_N} $T )',
    f'( ( ( X=$(echo hi) ) ); T=$(: "{_Q}"; echo token); {_N} $T )',
    # F2: the operand-side (verb+program) binding follows an alias, a transform and an append.
    f'T=$(: "{_Q}"; echo token; : {_N}); U=$T; {_N} $U',
    f'T=$(: "{_Q}"; echo token; : {_N}); U=${{T:0}}; {_N} $U',
    f'T=$(: "{_Q}"; echo token; : {_N}); U=; U+=$T; {_N} $U',
]

_NESTED_SCOPE_CONTROLS = [
    # A child subshell's assignment alone is no mint; the enclosing one runs nothing minty.
    f"( ( X=$(echo hi) ); {_N} status )",
    # A later rebinding that aliases nothing drops the operand binding.
    f'T=$(: "{_Q}"; echo token; : {_N}); U=$T; U=other; {_N} $U',
    f"( ( cd /tmp ) ); {_N} status",
    # Concatenation is not a whole-value alias: $U expands to more than the verb, so the
    # operand binding is not carried and these stay allowed (gpt r46).
    f'T=$(: "{_Q}"; echo token; : {_N}); U=prefix$T; {_N} $U',
    f'T=$(: "{_Q}"; echo token; : {_N}); U=${{T}}suffix; {_N} $U',
    f'T=$(: "{_Q}"; echo token; : {_N}); U=$T; U+=$T; {_N} $U',
    f'T=$(: "{_Q}"; echo token; : {_N}); U=$T; U+=tail; {_N} $U',
]


@pytest.mark.parametrize("cmd", _NESTED_SCOPE_MINTS)
def test_a_nested_or_aliased_substitution_mint_is_refused(cmd: str) -> None:
    assert security.is_denied(cmd), f"bash runs the mint: {cmd!r}"


@pytest.mark.parametrize("cmd", _NESTED_SCOPE_CONTROLS)
def test_the_nested_or_aliased_shapes_without_a_mint_stay_allowed(cmd: str) -> None:
    assert not security.is_denied(cmd), cmd
