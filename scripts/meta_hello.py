"""Meta Model API hello-world (no product logic)."""

import os
import sys


def main() -> int:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        print("MODEL_API_KEY is not set; export it and retry.", file=sys.stderr)
        return 2
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not installed; run: pip install -e .", file=sys.stderr)
        return 2

    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=key)
    resp = client.chat.completions.create(
        model="muse-spark-1.3",
        messages=[{"role": "user", "content": "Reply with exactly: hello"}],
        max_tokens=16,
    )
    print(resp.choices[0].message.content)
    print("model:", resp.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
