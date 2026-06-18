# Rex 🦖
### A Git-like Version Control System built from scratch in Java 21

Rex is a minimal VCS that replicates core Git internals — content-addressable object storage, tree-based snapshots, branching, and commit history — implemented entirely from scratch without any VCS libraries.

---

## Features

- `init` — Initialize a new Rex repository
- `add` — Stage files or directories for commit
- `commit` — Save a snapshot of staged files with a message and author
- `checkout` — Switch branches or restore a specific commit (supports detached HEAD)
- `log` — View the full commit history of the current branch

---

## How It Works

Rex follows the same core design Git uses internally:

**Object Storage**
Every file, directory snapshot, and commit is stored as an object identified by its SHA-1 hash. This means identical files are never stored twice — the same content always maps to the same hash.

**Three Object Types**
| Type | What it represents |
|---|---|
| `blob` | A single file's content |
| `tree` | A directory snapshot (holds blobs and other trees) |
| `commit` | A pointer to a tree + parent commit + message |

**On-disk format**
Objects are stored compressed (zlib) under `.rex/objects/`, split by the first two characters of their hash — exactly how Git does it.

**Branching**
Branches are simple files under `.rex/refs/heads/` containing a commit hash. `HEAD` either points to a branch ref (attached) or directly to a hash (detached HEAD).

---

## Getting Started

**Requirements:** Java 21 JDK

```bash
# Clone the repo
git clone https://github.com/your-username/rex.git
cd rex

# Compile
javac Rex.java
```

---

## Usage

```bash
# Initialize a new repository
java Rex init

# Stage files
java Rex add README.md
java Rex add src/

# Commit staged files
java Rex commit -m "initial commit"
java Rex commit -m "add feature" --author "Rohit <rohit@example.com>"

# View commit history
java Rex log

# Create and switch to a new branch
java Rex checkout feature --create

# Switch to an existing branch
java Rex checkout master

# Checkout a specific commit (detached HEAD)
java Rex checkout a3f9c12
```

---

## Project Structure

```
.
├── Rex.java          # Entire implementation (single file)
└── README.md
```

**Inside a Rex repository after init + commit:**
```
your-project/
├── .rex/
│   ├── HEAD                   # Points to current branch or commit
│   ├── index                  # Staged files (JSON)
│   ├── objects/               # All blobs, trees, commits (zlib compressed)
│   │   └── a3/
│   │       └── f9c12...       # Object stored by SHA-1 hash
│   └── refs/
│       └── heads/
│           └── master         # Branch pointer
└── your files...
```

---

## Design Decisions

**Why a single file?**
Rex is intentionally kept as one file (`Rex.java`) to make it easy to read top-to-bottom and understand the full flow without jumping between packages.

**Why no external libraries?**
The goal was to understand the internals — SHA-1 hashing, zlib compression, binary tree serialization — by implementing them using only the Java standard library.

**Limitations**
Rex is a learning project and does not implement:
- `diff` / `merge`
- Remote operations (`push`, `pull`, `clone`)
- `.rexignore` (like `.gitignore`)
