"""Source upload only: Vercel team tokens cannot use pull/build metadata APIs."""

import json
import os
import re
import subprocess
import sys


def main():
    for name in ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"):
        if not os.environ.get(name):
            print(f"::error::{name} is required")
            return 1

    # Also mask tokens supplied by callers through an expression other than secrets.
    token = os.environ["VERCEL_TOKEN"]
    escaped_token = token.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::add-mask::{escaped_token}", flush=True)
    try:
        build_env = json.loads(os.environ.get("VERCEL_BUILD_ENV", "{}"))
        if not isinstance(build_env, dict) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or not isinstance(value, str)
            for key, value in build_env.items()
        ):
            raise ValueError
    except (ValueError, TypeError):
        print("::error::build-env must be a JSON object of environment names and string values")
        return 1

    args = ["npx", "--yes", "vercel@59.15.1", "deploy", "--prod", "--yes"]
    for key, value in build_env.items():
        args.extend(["--build-env", f"{key}={value}"])
    args.append(f"--token={token}")
    # Arguments are never evaluated by a shell, including build environment values.
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        return result.returncode
    urls = re.findall(r"https://[a-zA-Z0-9.-]+\.vercel\.app\b", result.stdout)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"deployment-url={urls[-1] if urls else ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
