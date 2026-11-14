"""Cased-vs-lowered frame pairing for the git/tar self-dial floor.

The floor lowercases a line before its argv walk and reads a wrapper's options in the
case they were written (``sudo -H`` takes no value, ``sudo -h`` does). These pin that the
pairing does not desync when the lowered walk carries an alternative frame the cased walk
lacks, on the same ``sandbox-escape-git-tar-self`` rule as the ssh floor's sibling test.
"""

from __future__ import annotations

import pytest
from denied_commands_security_helpers import _own_address_probe_stays_local  # noqa: F401
from denied_commands_security_helpers import _denied_by

pytestmark = pytest.mark.usefixtures("fresh_darwin_libc_handles")


class TestGitTarSelfCaseFrames:
    """The cased/lowered frame pairing stays in step when the walks differ in length."""

    _RULE = "sandbox-escape-git-tar-self"

    @pytest.mark.parametrize(
        "cmd",
        [
            "F=$(which KiroCrew); echo $F; sudo -H git clone localhost:x",  # brand-ok
            "F=$(which KiroCrew); echo $F; bash -c 'sudo -H g?t clone local*:x'",  # brand-ok
            "F=$(which KiroCrew); echo $F; bash -c 'sudo -H t?r -cf local*:x .'",  # brand-ok
        ],
    )
    def test_a_cased_substitution_does_not_desync_the_git_tar_frames(self, cmd):
        # r32 (opus): a cased product name in a substitution body binds on the lowered
        # walk's alternative frame but not on the case-sensitive cased walk, so the two
        # frame lists differ in length. A length-gated positional pairing dropped every
        # cased frame and read ``sudo -H`` as ``-h``, which consumed ``git`` as its value
        # and allowed a self-dial main refuses. Each frame is now also read under every
        # cased frame that lowers to it. A mutant restoring the positional-only pairing
        # re-allows these and fails here.
        assert _denied_by(cmd) == self._RULE, cmd

    def test_a_cased_twin_keeps_an_option_that_takes_an_operand(self):
        # r33 (gpt): reading the lowered frame as well as its cased twin read
        # ``sudo --edit /tmp/git ...`` (sudoedit opens files, runs nothing) as a git run
        # and refused it. A frame with a cased twin is read only under its twins, so this
        # stays allowed, as on main.
        cmd = 'F=$(which KiroCrew); echo "$F"; sudo --edit /tmp/git clone localhost:x'  # brand-ok
        assert _denied_by(cmd) is None, cmd
