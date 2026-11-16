"""`make synth`: generate the synthetic merchant."""

import argparse
import sys
from datetime import date
from pathlib import Path

from revlane.synth import config as c
from revlane.synth.generate import DEFAULT_OUT, generate
from revlane.synth.world import GenerationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.synth", description=__doc__)
    parser.add_argument("--seed", type=int, default=c.DEFAULT_SEED)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="generation date, YYYY-MM-DD (default: today); the period is the 90 days ending on it",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="share of the full everyday volume to generate; the planted leaks stay the same size",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        result = generate(args.seed, args.as_of, args.scale, args.out)
    except GenerationError as error:
        print(f"Could not generate: {error}", file=sys.stderr)
        return 1

    key = result.answer_key
    print(f"Wrote {len(result.files)} files to {result.path}")
    print(f"  period         {key['period']['start']} to {key['period']['end']}")
    print(f"  fee month      {key['fee_month']}")
    print(f"  attempts       {result.attempts:,}")
    print(f"  leaks planted  {', '.join(leak['id'] for leak in key['leaks'])}")
    print(f"  traps planted  {', '.join(trap['id'] for trap in key['traps'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
