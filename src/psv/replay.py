"""Module — see functions for individual docstrings."""

# src/psv/replay.py
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ChainTruth:
    block_number: int
    block_hash: str
    timestamp: int
    chain_id: int
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemBelief:
    nonces: dict[str, int] = field(default_factory=dict)
    balances: dict[str, str] = field(default_factory=dict)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Frame:
    seq: int
    ts: float
    label: str
    chain_truth: ChainTruth
    system_belief: SystemBelief

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "label": self.label,
            "chain_truth": asdict(self.chain_truth),
            "system_belief": asdict(self.system_belief),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Frame:
        return cls(
            seq=int(d["seq"]),
            ts=float(d["ts"]),
            label=str(d.get("label", "")),
            chain_truth=ChainTruth(**d["chain_truth"]),
            system_belief=SystemBelief(**d["system_belief"]),
        )


class ReplayRecorder:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or time.strftime("%Y%m%dT%H%M%SZ")
        self.frames: list[Frame] = []
        self._seq = 0

    def record(
        self,
        chain_truth: ChainTruth,
        system_belief: SystemBelief,
        label: str = "",
    ) -> Frame:
        fr = Frame(
            seq=self._seq,
            ts=time.time(),
            label=label,
            chain_truth=chain_truth,
            system_belief=system_belief,
        )
        self.frames.append(fr)
        self._seq += 1
        return fr

    def dump(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "version": 1,
            "frame_count": len(self.frames),
            "frames": [f.to_dict() for f in self.frames],
        }
        raw = json.dumps(payload, indent=2, sort_keys=True)
        path.write_text(raw, encoding="utf-8")
        return path

    @staticmethod
    def load(path: str | Path) -> ReplayRecorder:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rec = ReplayRecorder(session_id=data.get("session_id"))
        for fd in data.get("frames", []):
            fr = Frame.from_dict(fd)
            rec.frames.append(fr)
            rec._seq = max(rec._seq, fr.seq + 1)
        return rec

    def playback(self) -> Iterator[Frame]:
        yield from sorted(self.frames, key=lambda f: f.seq)

    def fingerprint(self) -> str:
        canonical = json.dumps(
            [f.to_dict() for f in self.frames],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def assert_equal(self, other: ReplayRecorder) -> None:
        if self.fingerprint() != other.fingerprint():
            raise AssertionError(
                f"replay mismatch: {self.session_id} != {other.session_id} "
                f"({self.fingerprint()[:12]} vs {other.fingerprint()[:12]})"
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="psv-replay", description="PSV replay recorder/playback")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="create empty session skeleton")
    p_rec.add_argument("-o", "--output", required=True)
    p_rec.add_argument("--session-id")

    p_play = sub.add_parser("playback", help="print frames from session JSON")
    p_play.add_argument("path")
    p_play.add_argument("--json", action="store_true")

    p_fp = sub.add_parser("fingerprint", help="sha256 of canonical session")
    p_fp.add_argument("path")

    p_diff = sub.add_parser("diff", help="assert two sessions are identical")
    p_diff.add_argument("a")
    p_diff.add_argument("b")

    args = p.parse_args(argv)

    if args.cmd == "record":
        rec = ReplayRecorder(session_id=args.session_id)
        # skeleton frame for wiring tests
        rec.record(
            ChainTruth(block_number=0, block_hash="0x0", timestamp=0, chain_id=1),
            SystemBelief(),
            label="init",
        )
        out = rec.dump(args.output)
        print(f"wrote {out} frames={len(rec.frames)}")
        return 0

    if args.cmd == "playback":
        rec = ReplayRecorder.load(args.path)
        for fr in rec.playback():
            if args.json:
                print(json.dumps(fr.to_dict(), sort_keys=True))
            else:
                print(
                    f"#{fr.seq} ts={fr.ts:.3f} label={fr.label!r} "
                    f"block={fr.chain_truth.block_number} chain={fr.chain_truth.chain_id}"
                )
        return 0

    if args.cmd == "fingerprint":
        rec = ReplayRecorder.load(args.path)
        print(rec.fingerprint())
        return 0

    if args.cmd == "diff":
        a = ReplayRecorder.load(args.a)
        b = ReplayRecorder.load(args.b)
        try:
            a.assert_equal(b)
        except AssertionError as e:
            print(str(e), file=sys.stderr)
            return 1
        print("ok")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
