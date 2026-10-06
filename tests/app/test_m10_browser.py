"""M10 in a real browser: "Your patterns over time" on the entry page, the history line on M9's actions, the
evidence chain, and "Read these now" writing an explicit practice record. SYNTHETIC history (m10helpers)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from m10helpers import History, W, clear, ok, personal_baseline

from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.longitudinal.store import ProgressStore

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_your_patterns_over_time_in_the_browser(reader, lab, tmp_path):
    h = History(reader.store.root)
    personal_baseline(h, texts=5)                                   # /ɛ/ heard as /ɪ/ across 5 texts: likely personal
    for t in range(3):                                              # /s/ → /z/ recurring too
        h.read(f"sz{t}", clear("s", "z", W(f"sz{t}", 2)) + ok("s", W(f"szo{t}", 6)))
    for t in range(4):                                              # neutral texts: M9 needs a few more readings
        h.read(f"n{t}", ok("ʃ", W(f"na{t}", 6)), ok("ʃ", W(f"nb{t}", 6)))
    live = reader.coaching()
    assert live["state"] == "actions"
    server, thread = start_in_thread(lab, reader=reader)
    try:
        proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_progress.mjs"), server.url, CHROME,
                               str(tmp_path / "m10")], capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr[-4000:]
        r = json.loads(proc.stdout)
        e = r["entry"]
        assert e["visible"] and e["scope"] == "Based on all your readings" and e["progressAfterPractice"]
        assert e["groups"][0] == "practise" and {"sub:ɛ>ɪ", "sub:s>z"} <= {i["pattern"] for i in e["items"]}
        ɛ = next(i for i in e["items"] if i["pattern"] == "sub:ɛ>ɪ")
        assert ɛ["decision"] == "CONTINUE_CURRENT_TARGET" and "likely personal" in ɛ["text"]
        assert e["chain"][0].startswith("Pattern: ") and e["chain"][-1].startswith("Next: ")
        assert any(line.startswith("History: ") for line in e["chain"])
        assert e["history"] and all(x.startswith("Your history: ") for x in e["history"])     # M9 cards annotated
        assert "Nothing here is a score" in e["how"] and e["depth"].startswith("12 readings of 12 different texts")
        for banned in ("score:", "%", "rank", "streak", "mastered", "fixed forever", "caused"):
            assert banned not in e["text"].lower(), banned
        # "Read these now" → a practice session with an explicit, linked practice record
        sid = r["practiceSession"]["search"].split("session=")[1][:32]
        [rec] = [x for x in ProgressStore(reader.store.root).practice_records() if x["practice_session_id"] == sid]
        assert rec["advice"]["m9_target_id"] == r["practice"]["targetId"] and rec["target"]["patterns"]
        assert (tmp_path / "m10_progress.png").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
