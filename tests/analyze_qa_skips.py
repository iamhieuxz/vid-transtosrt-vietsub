"""Measure QA-skip rate and window timing from subtitle_translator.log.

Supports both log formats:
  old (before A-4 fix): "... qa_issues=6, context=yes)"
  new (after A-4 fix) : "... qa_issues=6, ctx_hist=y, ctx_tm=n, ctx_sem=n)"

Run from the repo root:
    python tests/analyze_qa_skips.py [path/to/log]

Post-fix expectation: "ignoring QA" disappears from logs, so the *predicted* skip
count below is a lower bound — it measures how often the OLD code would have
skipped, which is what you want for a before/after comparison.
"""
import io
import re
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LOG = sys.argv[1] if len(sys.argv) > 1 else "subtitle_translator.log"

# qa_issues is followed by either the old "context=yes" or the new ctx_* triple.
# \s+ everywhere: console-wrapped logs (Tee-Object) insert a newline mid-record.
PAT_OLD = re.compile(
    r"^(\S+ \S+).*Window (\d+): JA 3-pass OK\s+"
    r"\((\d+) lines,\s+qa_issues=(\d+),\s+context=(\w+)\)"
)
PAT_NEW = re.compile(
    r"^(\S+ \S+).*Window (\d+): JA 3-pass OK\s+"
    r"\((\d+) lines,\s+qa_issues=(\d+),\s+"
    r"ctx_hist=(\w+),\s+ctx_tm=(\w+),\s+ctx_sem=(\w+)\)"
)

def _open_log(path):
    """Open a log file, sniffing UTF-16 (PowerShell Tee-Object) vs UTF-8."""
    raw = open(path, "rb").read(4)
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return open(path, encoding="utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return open(path, encoding="utf-8-sig", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


rows = []          # (ts, win, n_lines, n_flags, ctx_tuple)
buf = ""           # carry for log lines wrapped by the console (Tee-Object)
for raw_line in _open_log(LOG):
    line = raw_line.rstrip("\n")
    if not line.strip():
        continue
    # A wrapped continuation: the previous line ended mid-record (no closing ')').
    if buf:
        line = buf + " " + line.strip()
        buf = ""

    m = PAT_NEW.search(line)
    if m:
        ts, win, n, q, h, t, s = m.groups()
        rows.append((ts, int(win), int(n), int(q), (h, t, s)))
        continue
    m = PAT_OLD.search(line)
    if m:
        ts, win, n, q, ctx = m.groups()
        rows.append((ts, int(win), int(n), int(q), (ctx,)))
        continue

    # Start of a record that got wrapped? keep it for the next iteration.
    if "JA 3-pass OK" in line and ")" not in line.split("JA 3-pass OK", 1)[1]:
        buf = line

print(f"log file                        : {LOG}")
print(f"total JA windows with 3-pass OK : {len(rows)}")
if not rows:
    sys.exit("no matching window lines found")

# Old skip rule (C-1, now removed): len >= 4 and flagged > len/2
def would_have_skipped(n, q):
    return n >= 4 and q > n / 2

skipped = [r for r in rows if would_have_skipped(r[2], r[3])]
applied = [r for r in rows if not would_have_skipped(r[2], r[3])]
print(f"  -> QA SKIPPED (old rule)      : {len(skipped)}  ({len(skipped)/len(rows)*100:.1f}%)")
print(f"  -> QA applied  (old rule)     : {len(applied)}  ({len(applied)/len(rows)*100:.1f}%)")

total_flags = sum(r[3] for r in rows)
lost = sum(min(r[3], r[2]) for r in skipped)
print(f"  total QA flags raised         : {total_flags}")
print(f"  flags the old rule discarded  : {lost}  ({lost/max(total_flags,1)*100:.1f}% of all flags)")

# Context-source breakdown (only possible with the new format)
new_fmt = [r for r in rows if len(r[4]) == 3]
if new_fmt:
    def pct(i):
        c = sum(1 for r in new_fmt if r[4][i] == "y")
        return f"{c}/{len(new_fmt)} ({c/len(new_fmt)*100:.0f}%)"
    print(f"\ncontext sources (new format, {len(new_fmt)} windows):")
    print(f"  ctx_hist (rolling history)    : {pct(0)}")
    print(f"  ctx_tm   (translation memory) : {pct(1)}")
    print(f"  ctx_sem  (Worker A semantic)  : {pct(2)}")
else:
    legacy_yes = sum(1 for r in rows if r[4][0] == "yes")
    print(f"\ncontext=yes (old format)        : {legacy_yes}/{len(rows)}")

# Timing cadence
def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f")

deltas = []
for a, b in zip(rows, rows[1:]):
    if b[1] == a[1] + 1:                      # consecutive windows in the same run
        d = (ts(b[0]) - ts(a[0])).total_seconds()
        if 0 < d < 600:
            deltas.append((d, b[2]))

if deltas:
    secs = [d for d, _ in deltas]
    lines = sum(n for _, n in deltas)
    print(f"\nconsecutive-window gaps        : {len(deltas)}")
    print(f"  mean per window              : {sum(secs)/len(secs):.1f}s")
    print(f"  min / max                    : {min(secs):.1f}s / {max(secs):.1f}s")
    print(f"  mean per subtitle line       : {sum(secs)/max(lines,1):.2f}s")
