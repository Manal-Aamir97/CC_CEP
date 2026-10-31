"""Download the three Verdet et al. (2025) AWS files the baseline needs (about 720 kB in total).

    python -m dataset.fetch_verdet

Files are taken from one fixed commit of github.com/averdet/master-terraform-sec-adoption and saved
to dataset/raw/verdet/, with MANIFEST.json recording the commit, sizes and SHA-256 hashes.
Costs one GitHub API request (to resolve the commit).
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from dataset import github

REPO = "averdet/master-terraform-sec-adoption"
FILES = ("aws/csv/dataset_repo_metadata.csv", "aws/csv/results.csv", "aws/csv/policies-aws-final.csv")
OUT = Path(__file__).resolve().parent / "raw" / "verdet"


def main():
    commit = github.get_json(f"/repos/{REPO}/commits/main")["sha"]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"source": f"https://github.com/{REPO}", "commit": commit,
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": []}
    for path in FILES:
        data = github.get_raw(REPO, commit, path)
        (OUT / Path(path).name).write_bytes(data)
        manifest["files"].append({"path": path, "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
        print(f"saved {Path(path).name} ({len(data):,} bytes)")
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"commit {commit}; manifest written to {OUT / 'MANIFEST.json'}")


if __name__ == "__main__":
    main()
