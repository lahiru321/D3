"""Entry point: `uv run d3` (or `python -m d3`).

  d3                     run the voice assistant
  d3 --text "pause"      run a typed command through the router + executor (no mic); repeatable
"""

import argparse


def run_text(texts: list[str], cfg: dict) -> None:
    """Run typed commands in order, e.g. --text "open the report" --text "second"."""
    from d3.executor import Executor
    from d3.handlers.media import MediaController
    from d3.handlers.volume import VolumeController
    from d3.resolver import Resolver
    from d3.router import keyword

    executor = Executor(MediaController(), VolumeController(cfg["volume"]["step"]), Resolver(cfg))
    for text in texts:
        intent = keyword.route(text)
        if intent is None:
            print(f"{text!r}: I didn't catch that")
            continue
        result = executor.run(intent)
        message = "Which one?" if result.choices else result.message
        print(f"{text!r}: {intent.name} {intent.args or ''} -> {'OK' if result.ok else 'X'} {message}")
        for n, (name, folder) in enumerate(result.choices or [], start=1):
            print(f"   {n}. {name}  -  {folder}")


def main() -> None:
    from d3.config import load_config

    parser = argparse.ArgumentParser(prog="d3", description="D3 voice-controlled desktop assistant")
    parser.add_argument("--text", action="append", help="run a typed command instead of listening (repeatable)")
    args = parser.parse_args()
    cfg = load_config()

    if args.text:
        run_text(args.text, cfg)
        return

    from d3.app import Assistant
    Assistant(cfg).run()


if __name__ == "__main__":
    main()
