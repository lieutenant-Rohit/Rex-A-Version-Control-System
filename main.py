#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import zlib
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional


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
    def deserialize(cls, data: bytes) -> "GitObject":
        decompressed = zlib.decompress(data)
        null_idx = decompressed.find(b"\0")
        if null_idx == -1:
            raise ValueError("Invalid object data")
        header = decompressed[:null_idx]
        content = decompressed[null_idx + 1 :]
        obj_type, _ = header.split(b" ")
        obj_type = obj_type.decode()
        if obj_type == "blob":
            return Blob(content)
        elif obj_type == "tree":
            return Tree.from_content(content)
        elif obj_type == "commit":
            return Commit.from_content(content)
        else:
            return GitObject(obj_type, content)


class Blob(GitObject):
    def __init__(self, content: bytes):
        super().__init__("blob", content)


class Tree(GitObject):
    def __init__(self, entries: Optional[List[Tuple[str, str, str]]] = None):
        self.entries = entries or []
        content = self._serialize_entries()
        super().__init__("tree", content)

    def _serialize_entries(self) -> bytes:
        content = b""
        for mode, name, obj_hash in sorted(self.entries, key=lambda e: e[1]):
            content += f"{mode} {name}\0".encode()
            content += bytes.fromhex(obj_hash)
        return content

    def add_entry(self, mode: str, name: str, obj_hash: str):
        self.entries.append((mode, name, obj_hash))
        self.content = self._serialize_entries()

    @classmethod
    def from_content(cls, content: bytes) -> "Tree":
        entries = []
        i = 0
        while i < len(content):
            null_idx = content.find(b"\0", i)
            if null_idx == -1:
                break
            mode_name = content[i:null_idx].decode()
            mode, name = mode_name.split(" ", 1)
            obj_hash = content[null_idx + 1 : null_idx + 21].hex()
            entries.append((mode, name, obj_hash))
            i = null_idx + 21
        return cls(entries)


class Commit(GitObject):
    def __init__(self, content: bytes):
        super().__init__("commit", content)

    @classmethod
    def from_content(cls, content: bytes) -> "Commit":
        return cls(content)

    def parse(self) -> Dict[str, str]:
        lines = self.content.decode().splitlines()
        result = {"message": ""}
        in_msg = False
        for line in lines:
            if in_msg:
                result["message"] += line + "\n"
            elif line.startswith("tree "):
                result["tree"] = line.split()[1]
            elif line.startswith("parent "):
                result["parent"] = line.split()[1]
            elif line.startswith("author "):
                result["author"] = line[7:]
            elif line == "":
                in_msg = True
        result["message"] = result["message"].strip()
        return result


class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()
        self.rex_dir = self.path / ".rex"
        self.objects_dir = self.rex_dir / "objects"
        self.refs_dir = self.rex_dir / "refs"
        self.heads_dir = self.refs_dir / "heads"
        self.head_file = self.rex_dir / "HEAD"
        self.index_file = self.rex_dir / "index"

    # ---------------- Setup ---------------- #
    def init(self) -> bool:
        if self.rex_dir.exists():
            return False
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.heads_dir.mkdir(parents=True, exist_ok=True)
        self.head_file.write_text("ref: refs/heads/master\n")
        self.save_index({})
        print(f"Initialized empty Rex repository in {self.path}")
        return True

    # ---------------- Object management ---------------- #
    def _object_path(self, obj_hash: str) -> Tuple[Path, Path]:
        return self.objects_dir / obj_hash[:2], self.objects_dir / obj_hash[:2] / obj_hash[2:]

    def store_object(self, obj: GitObject) -> str:
        obj_hash = obj.hash()
        obj_dir, obj_file = self._object_path(obj_hash)
        if not obj_file.exists():
            obj_dir.mkdir(parents=True, exist_ok=True)
            obj_file.write_bytes(obj.serialize())
        return obj_hash

    def read_object(self, obj_hash: str) -> GitObject:
        _, obj_file = self._object_path(obj_hash)
        data = obj_file.read_bytes()
        return GitObject.deserialize(data)

    # ---------------- Index management ---------------- #
    def load_index(self) -> Dict[str, str]:
        if not self.index_file.exists():
            return {}
        try:
            return json.loads(self.index_file.read_text())
        except:
            return {}

    def save_index(self, index: Dict[str, str]) -> None:
        self.index_file.write_text(json.dumps(index, indent=2))

    # ---------------- Add operations ---------------- #
    def add_file(self, path: str):
        full = self.path / path
        blob_hash = self.store_object(Blob(full.read_bytes()))
        index = self.load_index()
        index[path] = blob_hash
        self.save_index(index)
        print(f"Added {path}")

    def add_directory(self, path: str):
        full = self.path / path
        index = self.load_index()
        count = 0
        for f in full.rglob("*"):
            if f.is_file() and ".rex" not in f.parts:
                blob_hash = self.store_object(Blob(f.read_bytes()))
                index[str(f.relative_to(self.path))] = blob_hash
                count += 1
        self.save_index(index)
        print(f"Added {count} files from {path}")

    def add_path(self, path: str):
        full = self.path / path
        if full.is_file():
            self.add_file(path)
        elif full.is_dir():
            self.add_directory(path)

    # ---------------- Tree creation ---------------- #
    def create_tree_from_index(self) -> str:
        index = self.load_index()
        if not index:
            return self.store_object(Tree([]))
        root: Dict[str, object] = {}
        for path, blob_hash in index.items():
            parts = path.split("/")
            current = root
            for p in parts[:-1]:
                current = current.setdefault(p, {})
            current[parts[-1]] = blob_hash

        def build(d: Dict[str, object]) -> str:
            tree = Tree([])
            for name, val in sorted(d.items()):
                if isinstance(val, str):
                    tree.add_entry("100644", name, val)
                else:
                    tree.add_entry("40000", name, build(val))
            return self.store_object(tree)

        return build(root)

    # ---------------- Commit logic ---------------- #
    def _read_head_ref(self):
        if not self.head_file.exists():
            return None, None
        text = self.head_file.read_text().strip()
        if text.startswith("ref: "):
            ref = text[5:]
            ref_file = self.rex_dir / ref
            parent = ref_file.read_text().strip() if ref_file.exists() else None
            return ref, parent
        return None, text

    def commit(self, message: str, author: str = "Rex User <user@rex.com>"):
        tree_hash = self.create_tree_from_index()
        ref, parent = self._read_head_ref()
        now = int(time.time())
        tz = time.strftime("%z") or "+0000"
        author_line = f"{author} {now} {tz}"
        lines = [f"tree {tree_hash}"]
        if parent:
            lines.append(f"parent {parent}")
        lines += [f"author {author_line}", f"committer {author_line}", "", message]
        commit_obj = Commit("\n".join(lines).encode())
        commit_hash = self.store_object(commit_obj)
        if ref:
            (self.rex_dir / ref).write_text(commit_hash + "\n")
        else:
            self.head_file.write_text(commit_hash + "\n")
        print(f"[commit {commit_hash[:7]}] {message}")
        return commit_hash

    # ---------------- Helpers ---------------- #
    def _is_hash(self, s: str) -> bool:
        """Check if a string looks like a valid commit hash."""
        return len(s) >= 6 and all(c in "0123456789abcdef" for c in s.lower())

    def _read_commit_tree(self, commit_hash: str) -> str:
        commit_obj = self.read_object(commit_hash)
        for line in commit_obj.content.decode().splitlines():
            if line.startswith("tree "):
                return line.split()[1]
        raise ValueError("No tree found in commit")

    def _restore_tree(self, tree_hash: str, base: Path):
        tree = self.read_object(tree_hash)
        if not isinstance(tree, Tree):
            raise ValueError("Not a tree object")
        for mode, name, obj_hash in tree.entries:
            dest = base / name
            obj = self.read_object(obj_hash)
            if mode == "40000":
                dest.mkdir(exist_ok=True)
                self._restore_tree(obj_hash, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(obj.content)

    # ---------------- Checkout ---------------- #
    def checkout(self, name: str, create: bool = False):
        ref_file = self.heads_dir / name
        target_commit = None

        if ref_file.exists():
            target_commit = ref_file.read_text().strip()
            self.head_file.write_text(f"ref: refs/heads/{name}\n")
            print(f"Switched to branch '{name}'")
        elif self._is_hash(name):
            target_commit = name
            self.head_file.write_text(f"{name}\n")
            print(f"Note: detached HEAD at {name[:7]}")
        elif create:
            _, parent = self._read_head_ref()
            ref_file.write_text((parent or "") + "\n")
            self.head_file.write_text(f"ref: refs/heads/{name}\n")
            print(f"Created and switched to new branch '{name}'")
            return
        else:
            raise ValueError(f"Unknown branch/commit {name}")

        # 🧹 CLEAN WORKING DIRECTORY except .rex
        for item in self.path.iterdir():
            if item.name == ".rex":
                continue
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                for sub in item.rglob("*"):
                    if sub.is_file():
                        sub.unlink()
                for sub in sorted(item.rglob("*"), reverse=True):
                    if sub.is_dir():
                        try:
                            sub.rmdir()
                        except OSError:
                            pass
                try:
                    item.rmdir()
                except OSError:
                    pass

        tree_hash = self._read_commit_tree(target_commit)
        self._restore_tree(tree_hash, self.path)
        print(f"Checked out {name}")

    # ---------------- Log ---------------- #
    def log(self):
        _, head = self._read_head_ref()
        if not head:
            print("No commits yet.")
            return
        current = head.strip()
        while current:
            obj = self.read_object(current)
            if not isinstance(obj, Commit):
                break
            data = obj.parse()
            print(f"commit {current}")
            if "author" in data:
                parts = data["author"].split()
                if len(parts) > 2:
                    ts = parts[-2]
                    dt = datetime.utcfromtimestamp(int(ts))
                    print(f"Author: {' '.join(parts[:-2])}")
                    print(f"Date:   {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"\n    {data['message']}\n")
            current = data.get("parent")


# ---------------- CLI ---------------- #
def main():
    parser = argparse.ArgumentParser(description="Rex - Simple Git-like VCS")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init")
    addp = sub.add_parser("add"); addp.add_argument("paths", nargs="+")
    commitp = sub.add_parser("commit"); commitp.add_argument("-m", "--message", required=True); commitp.add_argument("--author")
    checkp = sub.add_parser("checkout"); checkp.add_argument("name"); checkp.add_argument("--create", action="store_true")
    sub.add_parser("log")

    args = parser.parse_args()
    repo = Repository()

    if args.cmd == "init":
        repo.init()
    elif args.cmd == "add":
        for p in args.paths:
            repo.add_path(p)
    elif args.cmd == "commit":
        repo.commit(args.message, args.author or "Rex User <user@rex.com>")
    elif args.cmd == "checkout":
        repo.checkout(args.name, create=args.create)
    elif args.cmd == "log":
        repo.log()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
