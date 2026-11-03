"""Save chat history to a private Hugging Face dataset repo.

Each server process appends records to its own JSONL file, and a
CommitScheduler uploads changed files in the background every minute.
On startup every saved file is downloaded and replayed.
"""

import json
import tempfile
import uuid
from pathlib import Path

from huggingface_hub import CommitScheduler, HfApi, snapshot_download

PATH_IN_REPO = "data"
UPLOAD_EVERY_MIN = 1


class HFHistory:
    def __init__(self, repo_id: str, token: str):
        HfApi(token=token).create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)

        work = Path(tempfile.mkdtemp(prefix="chat_history_"))
        download_dir = work / "download"
        snapshot_download(repo_id, repo_type="dataset", token=token, local_dir=download_dir,
                          allow_patterns=f"{PATH_IN_REPO}/*.jsonl")
        self._saved_files = sorted(download_dir.glob(f"{PATH_IN_REPO}/*.jsonl"))

        upload_dir = work / "upload"
        upload_dir.mkdir()
        self._file = upload_dir / f"{uuid.uuid4().hex}.jsonl"
        self._scheduler = CommitScheduler(repo_id=repo_id, repo_type="dataset", folder_path=upload_dir,
                                          path_in_repo=PATH_IN_REPO, every=UPLOAD_EVERY_MIN, token=token)

    def load(self) -> list[dict]:
        return read_records(self._saved_files)

    def append(self, record: dict) -> None:
        with self._scheduler.lock:
            with self._file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_records(files: list[Path]) -> list[dict]:
    records = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records
