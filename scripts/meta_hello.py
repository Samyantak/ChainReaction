"""Meta Model API hello-world (no product logic)."""

import sys


def main() -> int:
    try:
        from chainreaction.llm import base_url, chat, model_name
    except ImportError:
        print("openai package not installed; run: pip install -e .",
              file=sys.stderr)
        return 2
    try:
        print(chat("", "Reply with exactly: hello", max_tokens=4000))
    except RuntimeError as e:
        print(f"{e}", file=sys.stderr)
        return 2
    print(f"model: {model_name()} via {base_url()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
