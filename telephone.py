import argparse
import textwrap

from llm_tel.engine import cycle
from llm_tel.persistence import mk_exp_dir, Recorder

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Measure information degradation through LLM telephone.")
    ap.add_argument("--start", required=True, help="Seed answer text.")
    args = ap.parse_args(argv)
    exp_dir = mk_exp_dir(args.start)
    with Recorder(exp_dir) as r:
        cycle(args.start, recorder=r)

    print(textwrap.dedent(f"Degradation complete: results saved to {exp_dir}").strip())

if __name__ == "__main__":
    main()
