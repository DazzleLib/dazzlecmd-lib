"""Graded exit codes and the last-line verdict for safedel.

One definition of what each exit code means, and of the line a tool prints
LAST so that a caller who only looks at the tail of the output, or only at
the exit status, still learns the truth.

    0  everything staged
    1  unexpected error (nothing staged, an OS-level failure)
    2  usage error (argparse's own code)
    3  refused / aborted -- nothing moved (no --yes, a declined prompt, a
       same-name batch, a junction destination, a protected zone)
    4  partial -- some items staged, some not
    5  blocked by the line guard (a dangerous command chain)
    6  a post-condition check failed (for example, a link still present after
       unlinking, or an original that exists again)

Every non-zero code stops a shell ``&&`` chain; scripts can branch on the
value. The values and the line format are shared with the deletion guard
(``_guard.py``); a test pins the two together.

Stdlib only.
"""

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_PARTIAL = 4
EXIT_BLOCKED = 5
EXIT_POSTCONDITION = 6

EXIT_CODES = {
    EXIT_OK: "ok",
    EXIT_ERROR: "error",
    EXIT_USAGE: "usage",
    EXIT_REFUSED: "refused",
    EXIT_PARTIAL: "partial",
    EXIT_BLOCKED: "blocked",
    EXIT_POSTCONDITION: "postcondition-failed",
}

STAGED = "STAGED"
DRY_RUN = "DRY-RUN"
REFUSED = "REFUSED"
PARTIAL = "PARTIAL"
ERROR = "ERROR"
POSTCONDITION_FAILED = "POSTCONDITION-FAILED"


def verdict_line(tool, outcome, detail=""):
    """The LAST line a tool prints.

    verdict_line('safedel', 'STAGED', '2')              -> 'safedel: STAGED 2'
    verdict_line('safedel', 'REFUSED', '--yes not given') -> 'safedel: REFUSED --yes not given'

    The detail is flattened to one line: a verdict that wraps is a verdict
    that ``tail -1`` cuts in half.
    """
    detail = " ".join(str(detail).split())
    return ("%s: %s %s" % (tool, outcome, detail)).rstrip()


def result_verdict(result):
    """Map a finished ``TrashResult`` to ``(exit_code, outcome, detail)``.

    The order matters: a refusal is reported before anything else (nothing
    moved), a failed post-condition outranks a partial result (something is
    not as reported), and an error with nothing staged is not a "partial".
    """
    if getattr(result, "refused", False):
        reasons = list(getattr(result, "errors", []) or [])
        return EXIT_REFUSED, REFUSED, (reasons[0] if reasons else "refused")

    staged = getattr(result, "staged_count", 0)
    not_staged = getattr(result, "not_staged_count", 0)
    post = list(getattr(result, "postcondition_failures", []) or [])

    errors = list(getattr(result, "errors", []) or [])
    if post:
        return EXIT_POSTCONDITION, POSTCONDITION_FAILED, post[0]
    if staged and not not_staged and not errors:
        return EXIT_OK, STAGED, str(staged)
    if staged:
        # Something was staged, and something else went wrong. "STAGED" is
        # only ever said of a result with no errors at all -- the counts
        # alone are not trusted to tell a clean run from a damaged one.
        return (EXIT_PARTIAL, PARTIAL,
                "staged %d, not staged %d" % (staged, not_staged))
    return EXIT_ERROR, ERROR, (errors[0] if errors else "nothing was staged")
