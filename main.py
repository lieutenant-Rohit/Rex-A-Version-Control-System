from __future__ import annotations
import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path
from typing import Dict


class GitObject:
    def __init__(self, obj_type: str, content: bytes):
        self.type = obj_type
        self.content = content

    def hash(self) -> str:
        header = f"{self.type} {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content).hexdigest()

    def serialize(self) -> bytes:
        header = f"{self.type} {len(self.content)}\0".encode()
        return zlib.compress(header + self.content)

    @classmethod
    def deserialize(cls, data: bytes) -> GitObject:
        decompressed = zlib.decompress(data)
        null_idx = decompressed.find(b"\0")
        header = decompressed[:null_idx]
        content = decompressed[null_idx + 1:]
        obj_type, size = header.split(b" ")
        return cls(obj_type.decode(), content)


class Blob(GitObject):
    def __init__(self, content: bytes):
        super().__init__('blob', content)

    def get_content(self) -> bytes:
        return self.content


class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()
        self.rex_dir = self.path / ".rex"
        self.objects_dir = self.rex_dir / "objects"
        self.refs_dir = self.rex_dir / "refs"
        self.heads_dir = self.refs_dir / "heads"
        self.head_file = self.rex_dir / "HEAD"
        self.index_file = self.rex_dir / "index"

    def init(self) -> bool:
        if self.rex_dir.exists():
            return False

        self.rex_dir.mkdir()
        self.objects_dir.mkdir()
        self.refs_dir.mkdir()
        self.heads_dir.mkdir()

        self.head_file.write_text("ref: refs/heads/master\n")
        self.save_index({})

        print(f"Initialized empty Rex Repository in {self.path}")
        return True

    def store_object(self, obj: GitObject) -> str:
        obj_hash = obj.hash()
        obj_dir = self.objects_dir / obj_hash[:2]
        obj_file = obj_dir / obj_hash[2:]

        if not obj_file.exists():
            obj_dir.mkdir(exist_ok=True, parents=True)
            obj_file.write_bytes(obj.serialize())

        return obj_hash

    def load_index(self) -> Dict[str, str]:
        if not self.index_file.exists():
            return {}
        try:
            return json.loads(self.index_file.read_text())
        except:
            return {}

    def save_index(self, index: Dict[str, str]) -> None:
        self.index_file.write_text(json.dumps(index, indent=2))

    def add_file(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")

        content = full_path.read_bytes()
        blob = Blob(content)
        blob_hash = self.store_object(blob)
        index = self.load_index()
        index[path] = blob_hash
        self.save_index(index)
        print(f"Added {path}")

    def add_directory(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")
        if not full_path.is_dir():
            raise NotADirectoryError(f"Path {path} must be a directory")

        # recursively traverse the directory
        # create blob object for all file and rest is same as add files.
        index = self.load_index()
        added_count = 0
        for file_path in full_path.rglob("*"):
            if file_path.is_file():
                if ".rex" in file_path.parts:
                    continue

                content = file_path.read_bytes()
                blob = Blob(content)
                blob_hash = self.store_object(blob)
                rel_path = str(file_path.relative_to(self.path))
                index[rel_path] = blob_hash
                added_count += 1
        self.save_index(index)
        if added_count > 0:
            print(f"Added {added_count} files from directory {path}")
        else:
            print(f"Directory {path} already up-to-date")


    def add_path(self, path: str) -> None:
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"{path} does not exist")

        if full_path.is_file():
            self.add_file(path)
        elif full_path.is_dir():
            self.add_directory(path)
        else:
            raise ValueError(f"{path} is not a file or directory")


def main():
    parser = argparse.ArgumentParser(description="Rex - A Simple Version Control")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("init", help="Initialize a new repository")
    add_parser = subparsers.add_parser("add", help="Add files and directories to the staging area")
    add_parser.add_argument("paths", nargs="+", help="Paths to files or directories to add")
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    repo = Repository()

    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
        elif args.command == "add":
            if not repo.rex_dir.exists():
                print("Repository does not exist")
                return
            for path in args.paths:
                repo.add_path(path)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()