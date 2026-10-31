"""Fetch the Terraform files of selected projects, as they were before AI coding assistants.

    python -m dataset.fetch_files                         # main candidates in case_candidates.csv
    python -m dataset.fetch_files --role all              # main and reserve candidates
    python -m dataset.fetch_files --fill-gaps             # only the reserves needed to replace lost projects
    python -m dataset.fetch_files --cutoff 2021-06-29     # stricter: before the Copilot preview

For each project it finds the last commit on the default branch before the cutoff (default
2022-10-14, the day Verdet et al. scanned), lists that commit's .tf files and downloads only those
to dataset/raw/repos/<owner>__<name>/<commit>/. Two API requests per project; file downloads do not
count against the API limit. Every outcome is recorded in dataset/raw/repos/MANIFEST.json.
"""
import argparse
import csv
import hashlib
import http.client
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dataset import github
from dataset.baseline import RESULTS

OUT = Path(__file__).resolve().parent / "raw" / "repos"
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 300
DOWNLOAD_WORKERS = 8


def pinned_commit(repo, cutoff):
    """(sha, date) of the default branch's last commit before the cutoff, or None."""
    commits = github.get_json(f"/repos/{github.check_repo_name(repo)}/commits",
                              until=f"{cutoff}T00:00:00Z", per_page=1)
    if not commits:
        return None
    return commits[0]["sha"], commits[0]["commit"]["committer"]["date"]


def terraform_paths(repo, sha):
    tree = github.get_json(f"/repos/{repo}/git/trees/{sha}", recursive=1)
    paths = [item["path"] for item in tree.get("tree", [])
             if item.get("type") == "blob" and item["path"].endswith(".tf")
             and item.get("size", 0) <= MAX_FILE_BYTES]
    return sorted(paths)[:MAX_FILES], bool(tree.get("truncated")) or len(paths) > MAX_FILES


def fetch_repo(repo, cutoff, out_root=OUT):
    record = {"repo": repo, "cutoff": cutoff, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        pinned = pinned_commit(repo, cutoff)
    except github.NotFound:
        return {**record, "status": "not_found"}
    if pinned is None:
        return {**record, "status": "no_commit_before_cutoff"}
    sha, date = pinned
    record.update(commit=sha, commit_date=date)
    paths, partial = terraform_paths(repo, sha)
    if not paths:
        return {**record, "status": "no_terraform_files"}

    folder = out_root / repo.replace("/", "__") / sha

    def fetch_one(path):
        try:
            relative = github.safe_relative_path(path)
        except ValueError:
            return {"path": path, "status": "unsafe_path_skipped"}
        try:
            data = github.get_raw(repo, sha, path)
        except github.NotFound:
            return {"path": path, "status": "missing"}
        target = folder.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:   # raw files: no API limit
        files = list(pool.map(fetch_one, paths))
    return {**record, "status": "ok", "partial_listing": partial, "files": files}


def load_manifest():
    path = OUT / "MANIFEST.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", default=str(RESULTS / "case_candidates.csv"))
    parser.add_argument("--role", choices=["main", "reserve", "all"], default="main")
    parser.add_argument("--cutoff", default="2022-10-14", help="YYYY-MM-DD; commits before this day")
    parser.add_argument("--fill-gaps", action="store_true",
                        help="for main projects that could not be fetched, fetch reserves from the same group")
    args = parser.parse_args(argv)
    datetime.strptime(args.cutoff, "%Y-%m-%d")

    with open(args.candidates, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    manifest = load_manifest()
    OUT.mkdir(parents=True, exist_ok=True)

    def fetch_one_project(repo):
        key = f"{repo}@{args.cutoff}"
        if key in manifest:   # only final outcomes are saved; network failures are not
            return
        try:
            manifest[key] = fetch_repo(repo, args.cutoff)
        except (OSError, http.client.HTTPException) as exc:
            print(f"{repo}: network failure ({type(exc).__name__}); run again to retry", flush=True)
            return
        entry = manifest[key]
        print(f"{repo}: {entry['status']}" + (f", {len(entry['files'])} .tf files at {entry['commit'][:10]}"
                                               if entry["status"] == "ok" else ""), flush=True)
        save_manifest(manifest)  # after every project, so an interrupted run loses nothing

    def is_ok(repo):
        return manifest.get(f"{repo}@{args.cutoff}", {}).get("status") == "ok"

    try:
        if args.fill_gaps:
            groups = sorted({(r["resource"], r["status"]) for r in rows})
            for resource, status in groups:
                group = [r for r in rows if (r["resource"], r["status"]) == (resource, status)]
                wanted = sum(1 for r in group if r["role"] == "main")
                for reserve in (r for r in group if r["role"] == "reserve"):
                    if sum(1 for r in group if is_ok(r["repo"])) >= wanted:
                        break
                    fetch_one_project(reserve["repo"])
        else:
            for repo in dict.fromkeys(r["repo"] for r in rows if args.role in ("all", r["role"])):
                fetch_one_project(repo)
    except github.RateLimited as exc:
        print(f"stopped: {exc}. Run the same command again later; finished projects are skipped.")
    finally:
        save_manifest(manifest)


def save_manifest(manifest):
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
