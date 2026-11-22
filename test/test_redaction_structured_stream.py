"""Stream/whole parity and linear-time tests for the structured credential detector."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

import pytest
from redaction_structured_cases import (
    B64,
    PEM_BEGIN,
    PEM_BEGIN_PLAIN,
    PEM_BODY,
    PEM_END,
    PGP_BEGIN,
    PGP_END,
    assert_linear_work,
    aws_key_id,
    aws_secret,
    benign_cases,
    github_classic,
    jwt,
    secret_cases,
    session_token,
    synth,
)

from kiro_crew.security.redaction_structured import TAG, StreamRedactor, redact
from kiro_crew.security.redaction_structured.stream import CONTEXT

ALL_TEXTS = [(n, t) for n, t, _, _ in secret_cases()] + benign_cases()
ALL_TEXTS += [
    ("mixed", "\n".join(t for _, t in ALL_TEXTS[:12])),
    ("pair_across", f"{aws_secret()} " + "y" * 250 + f" {aws_key_id()} tail text"),
]


def run_stream(chunks: list[str]) -> tuple[str, list[tuple[int, int, str, str]]]:
    s = StreamRedactor()
    out = "".join(s.feed(c) for c in chunks) + s.flush()
    return out, s.spans


def split_at(text: str, cuts: list[int]) -> list[str]:
    bounds = [0, *sorted(cuts), len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]


@pytest.mark.parametrize("text", [pytest.param(t, id=n) for n, t in ALL_TEXTS])
def test_chunked_equals_whole_at_every_split_point(text: str) -> None:
    whole = redact(text)
    for cut in range(len(text) + 1):
        assert run_stream([text[:cut], text[cut:]]) == whole, f"split at {cut}"


@pytest.mark.parametrize("text", [pytest.param(t, id=n) for n, t in ALL_TEXTS])
def test_chunked_equals_whole_at_random_multi_splits(text: str) -> None:
    whole = redact(text)
    rng = random.Random(len(text))
    for _ in range(25):
        cuts = [rng.randrange(len(text) + 1) for _ in range(rng.randrange(2, 12))]
        assert run_stream(split_at(text, cuts)) == whole, cuts


@pytest.mark.parametrize("text", [pytest.param(t, id=n) for n, t in ALL_TEXTS[:20]])
def test_one_character_feeds(text: str) -> None:
    assert run_stream(list(text)) == redact(text)


def _long_cases() -> list[tuple[str, str]]:
    sess = session_token(6000, seed=7)
    return [
        ("pem_past_max_block", f"a {PEM_BEGIN}\n" + "B" * 17000 + "\nafter " * 20),
        ("pgp_unterminated_long", f"{PGP_BEGIN}\n" + PEM_BODY * 300),
        ("session_run_fail_closed", f"AWS_SESSION_TOKEN={sess}=\nnext {github_classic()}"),
        ("session_run_padded", f'{{"SessionToken": "{sess}=="}} ok'),
        ("long_jose_run", "eyJ" + "A" * 20000 + " " + jwt()),
        ("long_benign", ("plain words, a://b and arn:aws:s3:::bkt " * 600)),
        ("context_reach", "x" * (CONTEXT * 3) + f" {aws_key_id()} {aws_secret()}"),
    ]


@pytest.mark.parametrize("text", [pytest.param(t, id=n) for n, t in _long_cases()])
def test_long_units_match_whole_text(text: str) -> None:
    whole = redact(text)
    step = max(1, len(text) // 300)
    for cut in range(0, len(text) + 1, step):
        assert run_stream([text[:cut], text[cut:]]) == whole, f"split at {cut}"
    rng = random.Random(7)
    for _ in range(10):
        cuts = [rng.randrange(len(text) + 1) for _ in range(rng.randrange(5, 40))]
        assert run_stream(split_at(text, cuts)) == whole
    assert run_stream([text[i : i + 997] for i in range(0, len(text), 997)]) == whole


def test_session_run_tail_is_discarded_not_resumed() -> None:
    sess = session_token(9000, seed=3)
    text = f"AWS_SESSION_TOKEN={sess}\nafter"
    s = StreamRedactor()
    pieces = [s.feed(text[i : i + 512]) for i in range(0, len(text), 512)]
    out = "".join(pieces) + s.flush()
    assert out == f"AWS_SESSION_TOKEN={TAG}\nafter"
    assert sess[-50:] not in out


def test_pending_text_stays_bounded() -> None:
    s = StreamRedactor()
    longest = 0
    rng = random.Random(1)
    corpus = [t for _, t in ALL_TEXTS] + ["word " * 50, synth(400, B64, 3), "eyJ" + "Q" * 3000]
    for _ in range(400):
        s.feed(rng.choice(corpus) + "\n")
        longest = max(longest, len(s._pending))  # noqa: SLF001
    s.flush()
    # The largest hold is a private-key block (16,000 plus its END line) or a JOSE run.
    assert longest < 20_000


@pytest.mark.parametrize("unit", ["x", "X", "*"])
def test_a_mask_run_fed_in_pieces_is_not_held_past_its_bound(unit: str) -> None:
    s = StreamRedactor()
    longest = 0
    for _ in range(64):
        s.feed(unit * 4096)
        longest = max(longest, len(s._pending))  # noqa: SLF001
    s.flush()
    assert longest < 20_000


_HELD_LEADS = [
    "Cookie: session=",
    "X-Api-Key: ",
    "Authorization: Basic ",
    "curl -u admin:",
    "mysql -p",
    "machine h login u password ",
    'mysql --password="',
    "docker login -p '",
]
_ENTRIES = ["structured", "production"]
_ALNUM = "abcdefghijkLMNOP0123456789"
#: The longest chunk the held-value tests feed: a value of 4096 + this many characters
#: is still running past the bound at some chunk end, so it is always dropped.
_CHUNK_MAX = 900


def _held_value(n: int, rng: random.Random | None = None) -> str:
    rng = rng or random.Random(n)
    return "".join(rng.choice(_ALNUM) for _ in range(n))


def _new_stream(entry: str) -> Any:
    import kiro_crew.security as production

    return StreamRedactor() if entry == "structured" else production.StreamRedactor()


def _held_back(s: Any) -> int:
    """Characters a stream holds back."""
    return len(s._pending if isinstance(s, StreamRedactor) else s._buf)


def _fed(entry: str, chunks: list[str]) -> str:
    s = _new_stream(entry)
    return "".join(s.feed(c) for c in chunks) + s.flush()


def _pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _shows(out: str, value: str) -> bool:
    """Whether any 12-character piece of *value* is in *out*."""
    return any(value[k : k + 12] in out for k in range(0, len(value) - 12, 7))


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("lead", _HELD_LEADS)
def test_an_unfinished_header_value_is_masked_at_its_bound_not_held(entry: str, lead: str) -> None:
    value = _held_value(60_000)
    quote = lead[-1] if lead[-1] in "\"'" else ""
    text = f"{lead}{value}{quote} theme=dark\nafter"
    s = _new_stream(entry)
    out, longest = "", 0
    for piece in _pieces(text, 1024):
        out += s.feed(piece)
        longest = max(longest, _held_back(s))
    out += s.flush()
    assert longest <= 4096 + 1024
    # The mask, then nothing more of that line.
    assert out == f"{lead}{TAG}\nafter"


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("lead", ["Cookie: session=", "X-Api-Key: "])
def test_a_secret_split_across_the_hold_bound_never_streams_in_clear(entry: str, lead: str) -> None:
    value = _held_value(6000)
    text = f"{lead}{value}\nafter"
    bound = len(lead) + 4096
    for cut in range(bound - 40, bound + 40):
        assert _fed(entry, [text[:cut], text[cut:]]) == f"{lead}{TAG}\nafter", f"split at {cut}"


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("lead", ["Cookie: session=", "X-Api-Key: ", "curl -u admin:"])
@pytest.mark.parametrize("length", [4095, 4096, 4097])
def test_a_value_ending_at_the_hold_bound_is_masked_at_every_split(
    entry: str, lead: str, length: int
) -> None:
    # One character under the bound, at it and one over; the value's last character,
    # its end and the text after it each arrive in their own chunk. The value is
    # masked either way; the rest of its line stays only where the value ended before
    # a chunk end found it past the bound.
    text = f"{lead}{_held_value(length)} theme=dark\nafter"
    whole = redact(text)[0]
    assert whole == f"{lead}{TAG} theme=dark\nafter"
    end = len(lead) + length
    for cut in range(end - 4, end + 5):
        for chunks in (
            [text[:cut], text[cut:]],
            [text[:cut], text[cut : cut + 1], text[cut + 1 :]],
        ):
            assert _fed(entry, chunks) in (whole, f"{lead}{TAG}\nafter"), f"split at {cut}"


@pytest.mark.parametrize("entry", _ENTRIES)
def test_the_rest_of_a_held_value_s_line_goes_with_the_value(entry: str) -> None:
    # A session pair after the oversized value is never sent, at every chunk end that
    # leaves the value running past the bound; the next line streams as usual.
    later = session_token(64, seed=4)
    text = "Cookie: session=" + _held_value(5000) + f"; session_id={later}; theme=dark\nok\n"
    bound = len("Cookie: session=") + 4096
    end = len("Cookie: session=") + 5000
    cuts = [*range(bound, bound + 40), *range(bound + 40, end - 40, 13), *range(end - 40, end + 1)]
    for chunks in [[text[:cut], text[cut:]] for cut in cuts] + [
        _pieces(text, size) for size in (1, 7, 64, 256, 900)
    ]:
        assert _fed(entry, chunks) == f"Cookie: session={TAG}\nok\n", [len(c) for c in chunks[:3]]


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("lead", _HELD_LEADS)
def test_a_stream_that_ends_inside_a_held_value_sends_only_the_mask(entry: str, lead: str) -> None:
    for size in (1024, 4096, 9100):
        assert _fed(entry, _pieces(lead + _held_value(9000), size)) == lead + TAG


def _pinned() -> list[tuple[str, str, str]]:
    """``(id, text, secret)``: lines the review of this change broke an earlier design
    with. *secret* follows a value past the bound and the whole text masks it."""
    v = [_held_value(5100 + k) for k in range(3)]
    w = _held_value(30)
    block = f"{PEM_BEGIN}\n{PEM_BODY}\n{PEM_END}\n"
    return [
        ("session_cookie", f"Cookie: session={v[0]}; session_id={w}; theme=dark\n", w),
        ("curl_after_header", f"X-Api-Key: {v[0]} curl -u admin:{w} tail\n", w),
        ("inner_quoted_value", f"mysql --password=\"x curl -u 'admin:{v[0]}\" {w}' tail\n", w),
        ("two_values", f"X-Api-Key: {v[0]} curl -u admin:{v[1]} -u user:{w} tail\n", w),
        ("far_command_word", f"curl -u {'u' * 1200}:{v[0]} -u user:{w} tail\n", w),
        ("words_in_value", f'mysql --password="foo curl {v[0]} z" | curl -u user:{w} tail\n', w),
        ("pipes_in_value", f"mysql -pfoo|curl|{v[0]} -u user:{w} tail\n", w),
        ("long_word", f"env VAR={'v' * 2300} curl -u user:{v[0]} -u user:{w} tail\n", w),
        ("key_block", f"X-Api-Key: {v[0]}{block}tail\n", PEM_BODY[:40]),
        (
            "key_block_far",
            f"X-Api-Key: {v[0]} {PEM_BEGIN}{' ' * 1100}\n{PEM_BODY}\n",
            PEM_BODY[:40],
        ),
        ("token", f"X-Api-Key: {v[0]}.{github_classic()} tail\n", github_classic()[4:]),
        ("pair", f"X-Api-Key: {v[0]}.{aws_key_id()} then {aws_secret()} tail\n", aws_secret()),
    ]


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("text,secret", [pytest.param(t, s, id=n) for n, t, s in _pinned()])
def test_a_secret_after_a_held_value_is_never_sent(entry: str, text: str, secret: str) -> None:
    text += "next line, plain words\n"
    assert secret not in redact(text)[0]
    start = text.index(_held_value(5100))
    for chunks in [[text[: start + 4500], text[start + 4500 :]]] + [
        _pieces(text, size) for size in (_CHUNK_MAX, 333, 64, 7)
    ]:
        assert secret not in _fed(entry, chunks), [len(c) for c in chunks[:3]]


def _fuzz_line(rng: random.Random, secrets: list[str], dropped: list[str]) -> str:
    """One command or header line. Every credential value on it is short (the whole
    line stays inside the stream's look-behind, where stream and whole text agree) or
    long enough to be dropped at the bound; a value in between is followed only by
    secrets that need no label on the line."""

    def secret() -> str:
        secrets.append(_held_value(rng.randrange(20, 40), rng))
        return secrets[-1]

    def value(quoted: bool) -> tuple[str, bool]:
        n = rng.choice([6, 40, 150, 2500, 4090, 4095, 4096, 4097, 4200, 5000, 5003, 7000, 5100])
        text = _held_value(n, rng)
        if quoted and n > 40 and rng.random() < 0.7:
            at = rng.choice([3, 3, n // 2])
            text = text[:at] + rng.choice([" curl ", " /bin/curl ", "|curl|", " a b "]) + text[at:]
        return text, n <= 150 or n >= 4096 + _CHUNK_MAX

    free = [
        lambda: f"password={secret()}",
        lambda: f"{github_classic(seed=rng.randrange(99))[:4]}{secret()}{_held_value(6, rng)}",
        lambda: "plain words",
    ]
    scoped = [
        lambda: f"-u user:{secret()}",
        lambda: f"| curl -u user:{secret()}",
        lambda: f"; session_id={secret()}; theme=dark",
        lambda: f"--password={secret()}",
    ]
    lead = rng.choice(
        [
            "Cookie: a=",
            "X-Api-Key: ",
            "Authorization: Basic ",
            "curl -u user:",
            f"env VAR={_held_value(rng.choice([4, 900, 2300, 2300]), rng)} curl -u user:",
            f"env VAR={_held_value(2300, rng)} curl -u user:",
            "mysql -p",
            "mysql --password=",
            "mysql --password=",
            "docker login -p ",
        ]
    )
    quote = rng.choice(["", '"', "'"]) if lead.endswith(("password=", "-p ")) else ""
    held, exact = value(bool(quote))
    if exact and len(held) > 4096:
        dropped.append(held)
    lead, held = lead + quote, held + quote
    tail = [rng.choice(scoped if exact else free)() for _ in range(rng.randrange(1, 4))]
    return " ".join([lead + held, *tail])


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("seed", range(40))
def test_a_stream_sends_no_secret_the_whole_text_masks_after_a_held_value(
    entry: str, seed: int
) -> None:
    # Differential: random lines, values on both sides of the bound, random chunks.
    rng = random.Random(seed)
    for _ in range(8):
        secrets: list[str] = []
        dropped: list[str] = []
        lines = [_fuzz_line(rng, secrets, dropped) for _ in range(rng.randrange(1, 4))]
        text = "".join(line + "\n" for line in lines)
        whole = redact(text)[0]
        chunks, at = [], 0
        if dropped and rng.random() < 0.4:
            # One chunk up to a point past the bound inside the first dropped value.
            at = text.index(dropped[0]) + rng.randrange(4096, len(dropped[0]))
            chunks.append(text[:at])
        while at < len(text):
            size = rng.choice([1, 7, 64, 300, _CHUNK_MAX])
            chunks.append(text[at : at + rng.randrange(1, size + 1)])
            at += len(chunks[-1])
        s = _new_stream(entry)
        out, longest = "", 0
        for chunk in chunks:
            out += s.feed(chunk)
            longest = max(longest, _held_back(s))
        out += s.flush()
        assert longest <= 4096 + 2 * _CHUNK_MAX + CONTEXT
        for value in secrets:
            if value not in whole:
                assert value not in out, (seed, text[:200], [len(c) for c in chunks[:4]])


@pytest.mark.parametrize("entry", _ENTRIES)
def test_a_key_block_named_on_a_held_value_s_line_drops_the_rest_of_the_text(entry: str) -> None:
    # The BEGIN line is dropped with the value's line, and the whole text masks the
    # body by that line: nothing after it is sent, wherever the marker is split.
    body = (PEM_BODY * 3)[:644]
    text = "X-Api-Key: " + "aB5nQ7" * 800 + f"{PEM_BEGIN}\n{body}\n{PEM_END}\nafter"
    whole = redact(text)[0]
    assert body[:40] not in whole
    start = text.index(PEM_BEGIN)
    for cut in range(start - 3, start + len(PEM_BEGIN) + 3):
        # A chunk that ends after the value did reads the line as the whole text does.
        out = _fed(entry, [text[:cut], text[cut:]])
        assert out in ("X-Api-Key: " + TAG, whole), f"split at {cut}"
    assert _fed(entry, _pieces(text, 64)) == "X-Api-Key: " + TAG


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("lines", [170, 180, 185, 190])
def test_a_held_value_inside_an_unterminated_key_block_leaves_the_block_dropped(
    entry: str, lines: int
) -> None:
    # The block passes its bound while a header value inside it is past its own: the
    # block runs to the end of the text, so the value's line end must not end the drop.
    body = "\n".join(synth(64, B64, seed) for seed in range(lines))
    later = "\n".join(synth(64, B64, 900 + seed) for seed in range(40))
    text = f"{PEM_BEGIN}\n{body}\nX-Api-Key: {_held_value(4800)}\n{later}\n"
    whole = redact(text)[0]
    assert whole == f"{PEM_BEGIN}\n{TAG}"
    for size in (64, 700, 1024, 4096):
        assert _fed(entry, _pieces(text, size)) == whole, size


def test_a_value_ending_below_the_hold_bound_still_matches_whole_text() -> None:
    text = f"X-Api-Key: {_held_value(4000)}; theme=dark\nafter"
    assert run_stream(_pieces(text, 1024)) == redact(text)


def test_a_bearer_value_past_the_bound_keeps_the_older_ceiling() -> None:
    # The production stream's own 4 KiB ceiling reads a Bearer value too: it still
    # replaces the header with the value and sends what follows the value.
    text = "Authorization: Bearer " + "B" * 6000
    assert _fed("production", [text, "!after"]) == TAG + "!after"


# ── Linear work on adversarial input ──


def _adversarial() -> list[tuple[str, Callable[[int], str]]]:
    def fill(unit: str, lead: str = "") -> Callable[[int], str]:
        return lambda n: lead + (unit * (n // len(unit) + 1))[:n]

    return [
        ("ghp_near_miss", fill("ghp_" + "a" * 35 + " ")),
        ("ghp_glued", fill("ghp_")),
        ("eyj_dots", fill("eyJ.")),
        ("eyj_link_shape", fill("eyJ" + "A" * 96 + ".")),
        ("begin_headers", fill("-----" + "BEGIN ")),
        ("begin_typed", fill(PEM_BEGIN + " ")),
        ("key_ids_beside_secrets", fill(f"{aws_key_id()} {aws_secret()} ")),
        ("akia_eq", fill("AKIA=")),
        ("known_key_repeat", fill("aws_secret_access_key=")),
        ("known_key_json", fill('"SecretAccessKey": "')),
        ("arn_repeat", fill("arn:")),
        ("url_repeat", fill("https://")),
        ("url_nested_query", fill("a://h/?state=")),
        ("anthropic_near", fill("sk-ant-api0")),
        ("b64_run", fill("A")),
        ("session_run", fill("A", lead="SessionToken=")),
        ("mask_run_lower", fill("x")),
        ("mask_run_upper", fill("X")),
        ("star_run", fill("*")),
        ("plain", fill("lorem ipsum dolor ")),
        ("deep_quotes", fill('"\\"')),
        ("cookie_pairs", fill("cookie=AAAAAAAA; ")),
        ("cookie_header_pairs", fill("a=AAAAAAAA; cookie=B; ", lead="Cookie: ")),
        ("cookie_colon", fill("cookie: ")),
    ]


def _streamed(text: str) -> None:
    s = StreamRedactor()
    for i in range(0, len(text), 4096):
        s.feed(text[i : i + 4096])
    s.flush()


@pytest.mark.parametrize("build", [pytest.param(b, id=n) for n, b in _adversarial()])
def test_adversarial_input_costs_linear_work_whole(build: Callable[[int], str]) -> None:
    assert_linear_work(build, redact)


@pytest.mark.parametrize("build", [pytest.param(b, id=n) for n, b in _adversarial()])
def test_adversarial_input_costs_linear_work_streamed(build: Callable[[int], str]) -> None:
    assert_linear_work(build, _streamed)


@pytest.mark.parametrize("window", [' password="Zq8v', ' password="Zq8v Lm', ' password="Zq8v密'])
def test_an_open_unit_that_started_before_the_origin_still_holds(window: str) -> None:
    """The label sits in the look-behind (before ``origin``) and its value is
    still open, so the scan reports it open from ``origin`` instead of dropping it."""
    from kiro_crew.security.redaction_structured.engine import scan

    assert scan(window, final=False, origin=1).open_from == 1


def _glued_armour_headers() -> str:
    """A PGP block whose END line starts inside its BEGIN header runs to
    the next glued pair, past the look-behind; whole text then reads the EC header
    after it as unterminated and masks the tail."""
    pgp = PGP_BEGIN + PGP_END[5:]
    ec_begin = "-----BEGIN EC " "PRIVATE KEY-----"
    rows = "\\n".join(f"M{k:05d}Q" + synth(56, B64, 800 + k) for k in range(40))
    return (
        f"Cookie: theme={'a' * 9100}{pgp}\\n{rows}\\n{pgp}{ec_begin}"
        f"-----END {'X' * 58} "
        "PRIVATE KEY-----" + PEM_BEGIN_PLAIN + "\nTAIL M32702Z visible\n"
    )


@pytest.mark.parametrize("entry", ["structured", "production"])
@pytest.mark.parametrize(
    "first,size", [(0, 1), (0, 7), (0, 256), (0, 1024), (8200, 48), (8156, 1024), (8171, 4097)]
)
def test_a_block_past_the_look_behind_keeps_the_scan_where_whole_text_resumes_it(
    entry: str, first: int, size: int
) -> None:
    import kiro_crew.security as production

    text = _glued_armour_headers()
    whole = redact(text)[0]
    assert "M32702Z" not in whole
    s = StreamRedactor() if entry == "structured" else production.StreamRedactor()
    out = s.feed(text[:first]) if first else ""
    out += "".join(s.feed(text[i : i + size]) for i in range(first, len(text), size)) + s.flush()
    assert "M32702Z" not in out
    if entry == "structured":
        # The stream masks the cookie value at its hold bound and drops its line, which
        # names a key block: nothing after it is sent.
        assert out == "Cookie: theme=" + TAG


def test_a_completed_block_split_by_the_floor_keeps_its_retained_body_masked() -> None:
    import kiro_crew.security as production
    from kiro_crew.security.redaction_switch import always_scan

    ec_begin = "-----BEGIN EC " "PRIVATE KEY-----"
    text = (
        f"prefix {PEM_BEGIN}\nSYNTHETIC_ALPHA "
        + "q " * 2100
        + f"{ec_begin}\nSYNTHETIC_OMEGA\n{PEM_END}\n"
        + f"{PEM_BEGIN}\n{synth(64, B64, 41)}\n{PEM_END}\n"
    )
    with always_scan():
        whole = production.redact(text)
        stream = production.StreamRedactor()
        out = "".join(stream.feed(text[i : i + 64]) for i in range(0, len(text), 64))
        out += stream.flush()
    assert "SYNTHETIC_ALPHA" not in out
    assert "SYNTHETIC_OMEGA" not in out
    assert out == whole
