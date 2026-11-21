#!/usr/bin/env python3
"""Plans and builds the images the Image Benchmark workflow checks.

  plan.py plan <repo> [--base-ref SHA] [--output FILE] [--summary FILE]
  plan.py build <repo> --tag TAG        (the planned image comes from $IMAGE_ENTRY)

The workflow (.github/workflows/image-benchmark.yml) runs on every pull
request and merge group of every repository. `plan` decides what it checks,
from the repository's image list, .github/images.json:

  {
    "images": [
      {"key": "docker.io/archestra/platform", "dockerfile": "platform/Dockerfile", "context": "platform",
       "target": "unified", "build-args": {"NAME": "value"}, "prepare": ["bash", "scripts/context.sh"],
       "runner": "ubuntu-24.04-16core"}
    ],
    "unreleased": [{"dockerfile": "dev/*/Dockerfile", "reason": "Local development images, never released."}]
  }

`key` is the image's key in the release request (what benchmark waivers
match), `dockerfile` and `context` are paths in the repository; `target`,
`build-args`, `prepare` (a command run from the repository root before the
build, for a context the build needs generated), `build-args-file` (NAME=value
lines, read after `prepare`, before `build-args`) and `runner` (a
GitHub-hosted Linux x64 label) are optional. `unreleased` lists Dockerfiles
(paths or globs) that build no released image, with the reason.

An image is checked when the change touches its Dockerfile, its
.dockerignore, its images.json entry, or a waiver in
.github/image-benchmark-waivers.json that names it. Without --base-ref every
image is checked. Every Dockerfile the change adds or modifies must be
declared, under images or unreleased, and when images.json itself changes,
every Dockerfile in the repository must be; a repository with no Dockerfile
needs no images.json. Problems are reported as ::error:: lines and exit 1.

`build` builds one planned image for linux/amd64 into the Docker daemon,
without pushing it, after its `prepare` command.
"""

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

MANIFEST = ".github/images.json"
WAIVERS = ".github/image-benchmark-waivers.json"
DEFAULT_RUNNER = "ubuntu-latest"
# The names the supply-chain policy treats as Dockerfiles, minus BuildKit's
# per-Dockerfile ignore files (Dockerfile.dockerignore).
DOCKERFILE = re.compile(r"(^|/)(Dockerfile[^/]*|[^/]*\.dockerfile)$", re.IGNORECASE)
# GitHub-hosted Linux x64 runners only: Dockle runs on x86_64, and a label
# from the repository under check must not pick a self-hosted runner.
RUNNER = re.compile(r"^ubuntu-(latest|[0-9]{2}\.[0-9]{2}(-[0-9]+core)?)$")
KEY = re.compile(r"^[a-z0-9]+([._/-][a-z0-9]+)*(:[A-Za-z0-9_][A-Za-z0-9._-]*)?$")
TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IMAGE_FIELDS = {"key", "dockerfile", "context", "target", "build-args", "build-args-file", "prepare", "runner"}
PREPARE_TIMEOUT = 45 * 60


def main(argv):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument("repo")
    plan_command.add_argument("--base-ref", default="", help="Commit to compare with; empty checks every image")
    plan_command.add_argument("--output", default="",
                              help="File to append matrix=, build= and waivers= (the waivers file's path in the repository, or empty) to")
    plan_command.add_argument("--summary", default="", help="Markdown file to append the plan to")
    build_command = commands.add_parser("build")
    build_command.add_argument("repo")
    build_command.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        return build(Path(args.repo), json.loads(os.environ["IMAGE_ENTRY"]), args.tag)
    return run_plan(args)


def run_plan(args):
    repo = Path(args.repo)
    result = plan(repo, args.base_ref or None)
    for note in result["notes"]:
        print(note)
    for image in result["images"]:
        print(f"Check {image['key']} ({image['dockerfile']}): {'; '.join(image['reasons'])}")
    for error in result["errors"]:
        print(f"::error::{error}")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as summary:
            summary.write(markdown(result))
    if result["errors"]:
        return 1
    if args.output:
        matrix = {"include": [{field: image[field] for field in sorted(IMAGE_FIELDS)} for image in result["images"]]}
        waivers = WAIVERS if (repo / WAIVERS).is_file() else ""
        with open(args.output, "a", encoding="utf-8") as output:
            output.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
            output.write(f"build={'true' if result['images'] else 'false'}\n")
            output.write(f"waivers={waivers}\n")
    return 0


def plan(repo, base):
    """What to check: {"images": [entry + "reasons"], "errors": [...], "notes": [...]}.

    base is the commit to compare HEAD with, or None to check every image."""
    result = {"images": [], "errors": [], "notes": []}
    dockerfiles = tracked_dockerfiles(repo)
    changed = None if base is None else changed_files(repo, base)
    head_text = (repo / MANIFEST).read_text(encoding="utf-8") if (repo / MANIFEST).is_file() else None

    if head_text is None:
        if not dockerfiles:
            result["notes"].append("No Dockerfile; nothing to check.")
            return result
        touched = dockerfiles if changed is None else [path for path in dockerfiles if path in changed]
        for path in touched:
            result["errors"].append(undeclared_message(path))
        if not touched:
            result["notes"].append(f"No Dockerfile changed, and none is declared in {MANIFEST}.")
        return result

    try:
        manifest = load_manifest(head_text, MANIFEST)
    except ValueError as error:
        result["errors"].append(str(error))
        return result
    result["errors"] += check_paths(repo, manifest, dockerfiles)
    try:
        benchmark_module().load_waivers(str(repo / WAIVERS) if (repo / WAIVERS).is_file() else "")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result["errors"].append(f"{WAIVERS}: {error}")

    images = {image["dockerfile"] for image in manifest["images"]}
    full_check = changed is None or MANIFEST in changed
    for path in dockerfiles:
        if not full_check and path not in changed:
            continue
        if path not in images and not any(fnmatch.fnmatchcase(path, entry["dockerfile"]) for entry in manifest["unreleased"]):
            result["errors"].append(undeclared_message(path))

    if changed is None:
        selected = [(image, ["every image is checked"]) for image in manifest["images"]]
    else:
        base_entries = base_manifest_entries(repo, base)
        waived_keys = changed_waiver_patterns(repo, base)
        selected = []
        for image in manifest["images"]:
            reasons = []
            if image["dockerfile"] in changed:
                reasons.append("its Dockerfile changed")
            if ignore_files(image) & changed:
                reasons.append("its .dockerignore changed")
            if image["key"] not in base_entries:
                reasons.append(f"it is new in {MANIFEST}")
            elif base_entries[image["key"]] != image:
                reasons.append(f"its {MANIFEST} entry changed")
            if waived_keys is None or any(fnmatch.fnmatchcase(image["key"], pattern) for pattern in waived_keys):
                reasons.append("its waivers changed")
            if reasons:
                selected.append((image, reasons))
    result["images"] = [{**normalized(image), "reasons": reasons} for image, reasons in selected]
    if not result["images"] and not result["errors"]:
        result["notes"].append("No image's Dockerfile, build or waivers changed; nothing to build.")
    return result


def build(repo, entry, tag):
    """Builds one planned image for linux/amd64 into the Docker daemon."""
    # The plan's matrix entry, with its defaults dropped, checked again.
    entry = {field: value for field, value in entry.items() if field in IMAGE_FIELDS and value not in ("", [], {})}
    image = normalized(validate_image(entry, "image"))
    if image["prepare"]:
        print(f"Preparing {image['key']}: {' '.join(image['prepare'])}", flush=True)
        subprocess.run(image["prepare"], cwd=repo, check=True, timeout=PREPARE_TIMEOUT)
    command = ["docker", "build", "--platform", "linux/amd64", "--file", str(repo / image["dockerfile"]), "--tag", tag]
    if image["target"]:
        command += ["--target", image["target"]]
    args = read_build_args(repo, image["build-args-file"]) if image["build-args-file"] else {}
    for name, value in sorted({**args, **image["build-args"]}.items()):
        command += ["--build-arg", f"{name}={value}"]
    command.append(str(repo / image["context"]))
    print(f"Building {image['key']}: {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)
    return 0


def load_manifest(text, source):
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON: {error}") from None
    if not isinstance(document, dict) or not document or not set(document) <= {"images", "unreleased"}:
        raise ValueError(f"{source} must be an object with an images list, an unreleased list, or both")
    images, unreleased = document.get("images", []), document.get("unreleased", [])
    if not isinstance(images, list) or not isinstance(unreleased, list):
        raise ValueError(f"{source}: images and unreleased must be lists")
    images = [validate_image(image, f"{source}: images[{index}]") for index, image in enumerate(images)]
    keys = [image["key"] for image in images]
    for key in sorted({key for key in keys if keys.count(key) > 1}):
        raise ValueError(f"{source}: {key} is listed more than once")
    for index, entry in enumerate(unreleased):
        where = f"{source}: unreleased[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"dockerfile", "reason"}:
            raise ValueError(f"{where} must have exactly dockerfile and reason")
        relative_path(entry["dockerfile"], f"{where}: dockerfile")
        if not isinstance(entry["reason"], str) or len(entry["reason"].strip()) < 10:
            raise ValueError(f"{where}: reason must say why the Dockerfile builds no released image")
    return {"images": images, "unreleased": unreleased}


def validate_image(image, where):
    if not isinstance(image, dict) or not {"key", "dockerfile", "context"} <= set(image) <= IMAGE_FIELDS:
        raise ValueError(f"{where} must have key, dockerfile and context, and only "
                         + ", ".join(sorted(IMAGE_FIELDS - {"key", "dockerfile", "context"})) + " besides")
    if not isinstance(image["key"], str) or not KEY.match(image["key"]):
        raise ValueError(f"{where}: key must be the image's key in the release request, such as docker.io/archestra/platform")
    relative_path(image["dockerfile"], f"{where}: dockerfile")
    relative_path(image["context"], f"{where}: context", allow_root=True)
    if "target" in image and (not isinstance(image["target"], str) or not TARGET.match(image["target"])):
        raise ValueError(f"{where}: target must be a build stage name")
    args = image.get("build-args", {})
    if not isinstance(args, dict) or not all(
            isinstance(name, str) and ARG_NAME.match(name) and isinstance(value, str) and not re.search(r"[\n\r\0]", value)
            for name, value in args.items()):
        raise ValueError(f"{where}: build-args must map argument names to single-line strings")
    if "build-args-file" in image:
        relative_path(image["build-args-file"], f"{where}: build-args-file")
    prepare = image.get("prepare", [])
    if not isinstance(prepare, list) or ("prepare" in image and not prepare) or not all(
            isinstance(part, str) and part and "\0" not in part for part in prepare):
        raise ValueError(f"{where}: prepare must be a non-empty command list, such as [\"bash\", \"scripts/context.sh\"]")
    if "runner" in image and (not isinstance(image["runner"], str) or not RUNNER.match(image["runner"])):
        raise ValueError(f"{where}: runner must be a GitHub-hosted Linux x64 label, such as ubuntu-latest or ubuntu-24.04-16core")
    return image


def read_build_args(repo, path):
    """NAME=value lines; blank lines and # comments are skipped."""
    args = {}
    for number, line in enumerate((repo / path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not ARG_NAME.match(name) or "\0" in value:
            raise ValueError(f"{path}:{number}: expected NAME=value")
        args[name] = value
    return args


def relative_path(value, where, allow_root=False):
    path = PurePosixPath(value) if isinstance(value, str) and value else None
    if path is None or path.is_absolute() or ".." in path.parts or "\\" in value or (str(path) == "." and not allow_root):
        raise ValueError(f"{where} must be a path in the repository, relative to its root")


def check_paths(repo, manifest, dockerfiles):
    errors = []
    unreleased_dockerfiles = set()
    for entry in manifest["unreleased"]:
        matches = [path for path in dockerfiles if fnmatch.fnmatchcase(path, entry["dockerfile"])]
        if not matches:
            errors.append(f"{MANIFEST}: unreleased {entry['dockerfile']} matches no Dockerfile; remove it")
        unreleased_dockerfiles.update(matches)
    for image in manifest["images"]:
        if not (repo / image["dockerfile"]).is_file():
            errors.append(f"{MANIFEST}: {image['key']}: {image['dockerfile']} does not exist; update or remove the entry")
        elif image["dockerfile"] in unreleased_dockerfiles:
            errors.append(f"{MANIFEST}: {image['dockerfile']} is listed under both images and unreleased")
        # A prepare command may generate the context.
        if "prepare" not in image and not (repo / image["context"]).is_dir():
            errors.append(f"{MANIFEST}: {image['key']}: context {image['context']} is not a directory")
    return errors


def normalized(image):
    return {"key": image["key"], "dockerfile": image["dockerfile"], "context": image["context"],
            "target": image.get("target", ""), "build-args": image.get("build-args", {}),
            "build-args-file": image.get("build-args-file", ""),
            "prepare": image.get("prepare", []), "runner": image.get("runner", DEFAULT_RUNNER)}


def ignore_files(image):
    context = PurePosixPath(image["context"])
    return {(context / ".dockerignore").as_posix(), f"{image['dockerfile']}.dockerignore"}


def undeclared_message(path):
    return (f"{path} is not declared in {MANIFEST}. Add it under images, with the key it is released as, so "
            "its image is checked against the benchmark, or under unreleased, with the reason it builds no "
            "released image.")


def base_manifest_entries(repo, base):
    """The base commit's images by key; empty when it had no valid images.json."""
    text = show(repo, base, MANIFEST)
    if text is None:
        return {}
    try:
        return {image["key"]: image for image in load_manifest(text, MANIFEST)["images"]}
    except ValueError:
        return {}


def changed_waiver_patterns(repo, base):
    """Image globs of the waivers added, removed or changed since base; None when any image may be affected."""
    head = (repo / WAIVERS).read_text(encoding="utf-8") if (repo / WAIVERS).is_file() else None
    before = show(repo, base, WAIVERS)
    if head == before:
        return set()
    head_waivers, base_waivers = waiver_list(head), waiver_list(before)
    if head_waivers is None or base_waivers is None:
        return None
    canonical = lambda waivers: {json.dumps(waiver, sort_keys=True) for waiver in waivers}
    patterns = set()
    for waiver in canonical(head_waivers) ^ canonical(base_waivers):
        images = json.loads(waiver).get("images")
        if not isinstance(images, list) or not all(isinstance(image, str) for image in images):
            return None
        patterns.update(images)
    return patterns


def waiver_list(text):
    if text is None:
        return []
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    waivers = document.get("waivers") if isinstance(document, dict) else None
    return waivers if isinstance(waivers, list) and all(isinstance(waiver, dict) for waiver in waivers) else None


def tracked_dockerfiles(repo):
    files = git(repo, "ls-files", "-z").split("\0")
    return sorted(path for path in files if DOCKERFILE.search(path) and not path.lower().endswith(".dockerignore"))


def changed_files(repo, base):
    return set(filter(None, git(repo, "diff", "--name-only", "--no-renames", "-z", base, "HEAD").split("\0")))


def show(repo, ref, path):
    """A file's content at ref, or None if it has none."""
    if subprocess.run(["git", "cat-file", "-e", f"{ref}:{path}"], cwd=repo, capture_output=True).returncode:
        return None
    return git(repo, "show", f"{ref}:{path}")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def markdown(result):
    lines = ["### Image benchmark plan", ""]
    lines += [f"- {note}" for note in result["notes"]]
    lines += [f"- Check `{image['key']}` (`{image['dockerfile']}`): {'; '.join(image['reasons'])}" for image in result["images"]]
    lines += [f"- :x: {error}" for error in result["errors"]]
    return "\n".join(lines) + "\n\n"


def benchmark_module():
    spec = importlib.util.spec_from_file_location("image_benchmark", Path(__file__).with_name("benchmark.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        sys.exit(f"::error::image-benchmark: {error}")
