🦖 Rex — A Simple Git-like Version Control System

Rex is a lightweight, Git-inspired version control system built in Python. It implements core version control concepts such as blobs, trees, commits, branching, and checkout — helping you understand how Git works internally.

⸻

🚀 Features

* Initialize a repository (init)
* Track files and directories (add)
* Create commits with history tracking (commit)
* View commit history (log)
* Switch branches or commits (checkout)
* Branch creation support
* Object storage using SHA-1 hashing
* Compression using zlib (just like Git!)

⸻

🧠 How It Works

Rex mimics Git’s internal architecture:

* Blob → Stores file content
* Tree → Represents directory structure
* Commit → Tracks snapshots with metadata
* Objects → Stored using SHA-1 hashes
* Index → Tracks staged files
* HEAD → Points to current branch/commit

All data is stored inside a hidden .rex/ directory.

📦 Installation
# Clone the repository
git clone https://github.com/lieutenant-Rohit/rex.git

# Go into project folder
cd rex

# Check Python installation
python3 --version

# Run the project
python3 rex.py

# (Optional) Make executable
chmod +x rex.py
./rex.py

🔍 Example Usage
# Initialize repository
./rex.py init

# Create a file
echo "Hello Rex" > file.txt

# Add file to staging
./rex.py add file.txt

# Commit changes
./rex.py commit -m "First commit"

# View commit history
./rex.py log

# Create and switch to a new branch
./rex.py checkout feature --create

# Modify file
echo "New change" >> file.txt

# Add and commit again
./rex.py add file.txt
./rex.py commit -m "Updated file"

# Switch back to master
./rex.py checkout master
