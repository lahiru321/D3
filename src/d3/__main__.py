"""Entry point: `uv run d3` (or `python -m d3`).

  d3                     run the voice assistant
  d3 --text "pause"      run one typed command through the router + executor (no mic)
"""

import argparse


def run_text(text: str, cfg: dict) -> None:
    from d3.executor import Executor
    from d3.handlers.media import MediaController
    from d3.handlers.volume import VolumeController
    from d3.router import keyword

    intent = keyword.route(text)
    if intent is None:
        print("I didn't catch that")
        return
    ok, message = Executor(MediaController(), VolumeController(cfg["volume"]["step"])).run(intent)
    print(f"{intent.name} {intent.args or ''} -> {'OK' if ok else 'X'} {message}")


def main() -> None:
    from d3.config import load_config

    parser = argparse.ArgumentParser(prog="d3", description="D3 voice-controlled desktop assistant")
    parser.add_argument("--text", help="run one typed command instead of listening")
    args = parser.parse_args()
    cfg = load_config()

    if args.text:
        run_text(args.text, cfg)
        return

    from d3.app import Assistant
    Assistant(cfg).run()


if __name__ == "__main__":
    main()
