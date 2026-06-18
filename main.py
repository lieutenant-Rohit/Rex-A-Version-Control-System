import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.stream.Stream;
import java.util.zip.DeflaterOutputStream;
import java.util.zip.InflaterInputStream;

/**
 * Rex — a simple Git-like VCS, ported to Java 21.
 *
 * Usage:
 *   java Rex init
 *   java Rex add <path> [<path> ...]
 *   java Rex commit -m <message> [--author <name <email>>]
 *   java Rex checkout <branch-or-hash> [--create]
 *   java Rex log
 */
public class Rex {

    // ─────────────────────────────────────────────
    // Git object model
    // ─────────────────────────────────────────────

    /** Base class for all stored objects (blob, tree, commit). */
    static class GitObject {
        final String type;
        byte[] content;

        GitObject(String type, byte[] content) {
            this.type = type;
            this.content = content;
        }

        /** SHA-1 of  "<type> <len>\0<content>". */
        String hash() {
            try {
                MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
                sha1.update(header());
                sha1.update(content);
                return bytesToHex(sha1.digest());
            } catch (NoSuchAlgorithmException e) {
                throw new RuntimeException(e);
            }
        }

        /** zlib-compressed raw object bytes. */
        byte[] serialize() {
            byte[] hdr = header();
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            try (DeflaterOutputStream dos = new DeflaterOutputStream(baos)) {
                dos.write(hdr);
                dos.write(content);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            return baos.toByteArray();
        }

        private byte[] header() {
            return (type + " " + content.length + "\0").getBytes(StandardCharsets.UTF_8);
        }

        /** Decompress and dispatch to the correct subtype. */
        static GitObject deserialize(byte[] compressed) {
            byte[] raw = decompress(compressed);
            int nullIdx = indexOf(raw, (byte) '\0', 0);
            if (nullIdx == -1) throw new IllegalArgumentException("Invalid object data");

            String headerStr = new String(raw, 0, nullIdx, StandardCharsets.UTF_8);
            byte[] body = Arrays.copyOfRange(raw, nullIdx + 1, raw.length);
            String objType = headerStr.split(" ")[0];

            return switch (objType) {
                case "blob"   -> new Blob(body);
                case "tree"   -> Tree.fromContent(body);
                case "commit" -> Commit.fromContent(body);
                default       -> new GitObject(objType, body);
            };
        }

        // ── Utilities ──────────────────────────────

        static byte[] decompress(byte[] data) {
            try (InflaterInputStream iis = new InflaterInputStream(new ByteArrayInputStream(data));
                 ByteArrayOutputStream baos = new ByteArrayOutputStream()) {
                iis.transferTo(baos);
                return baos.toByteArray();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }

        static String bytesToHex(byte[] bytes) {
            StringBuilder sb = new StringBuilder(bytes.length * 2);
            for (byte b : bytes) sb.append(String.format("%02x", b));
            return sb.toString();
        }

        static byte[] hexToBytes(String hex) {
            int len = hex.length();
            byte[] out = new byte[len / 2];
            for (int i = 0; i < len; i += 2)
                out[i / 2] = (byte) Integer.parseInt(hex.substring(i, i + 2), 16);
            return out;
        }

        /** Find first occurrence of needle in haystack starting at from. */
        static int indexOf(byte[] haystack, byte needle, int from) {
            for (int i = from; i < haystack.length; i++)
                if (haystack[i] == needle) return i;
            return -1;
        }
    }

    // ─────────────────────────────────────────────

    static class Blob extends GitObject {
        Blob(byte[] content) { super("blob", content); }
    }

    // ─────────────────────────────────────────────

    static class Tree extends GitObject {
        /** (mode, name, hash) entries. */
        final List<String[]> entries;

        Tree(List<String[]> entries) {
            super("tree", serializeEntries(entries));
            this.entries = entries;
        }

        void addEntry(String mode, String name, String hash) {
            entries.add(new String[]{mode, name, hash});
            // keep content in sync
            content = serializeEntries(entries);
        }

        private static byte[] serializeEntries(List<String[]> entries) {
            // sort by name (index 1)
            List<String[]> sorted = new ArrayList<>(entries);
            sorted.sort(Comparator.comparing(e -> e[1]));
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            for (String[] e : sorted) {
                String mode = e[0], name = e[1], hash = e[2];
                try {
                    baos.write((mode + " " + name + "\0").getBytes(StandardCharsets.UTF_8));
                    baos.write(hexToBytes(hash));
                } catch (IOException ex) {
                    throw new UncheckedIOException(ex);
                }
            }
            return baos.toByteArray();
        }

        static Tree fromContent(byte[] content) {
            List<String[]> entries = new ArrayList<>();
            int i = 0;
            while (i < content.length) {
                int nullIdx = indexOf(content, (byte) '\0', i);
                if (nullIdx == -1) break;
                String modeName = new String(content, i, nullIdx - i, StandardCharsets.UTF_8);
                String[] parts = modeName.split(" ", 2);
                String mode = parts[0], name = parts[1];
                byte[] hashBytes = Arrays.copyOfRange(content, nullIdx + 1, nullIdx + 21);
                String hash = bytesToHex(hashBytes);
                entries.add(new String[]{mode, name, hash});
                i = nullIdx + 21;
            }
            return new Tree(entries);
        }
    }

    // ─────────────────────────────────────────────

    static class Commit extends GitObject {
        Commit(byte[] content) { super("commit", content); }

        static Commit fromContent(byte[] content) { return new Commit(content); }

        /** Parse commit content into a map: tree, parent?, author, message. */
        Map<String, String> parse() {
            Map<String, String> result = new LinkedHashMap<>();
            result.put("message", "");
            String text = new String(content, StandardCharsets.UTF_8);
            String[] lines = text.split("\n", -1);
            boolean inMsg = false;
            StringBuilder msgBuilder = new StringBuilder();
            for (String line : lines) {
                if (inMsg) {
                    msgBuilder.append(line).append("\n");
                } else if (line.startsWith("tree ")) {
                    result.put("tree", line.substring(5));
                } else if (line.startsWith("parent ")) {
                    result.put("parent", line.substring(7));
                } else if (line.startsWith("author ")) {
                    result.put("author", line.substring(7));
                } else if (line.isEmpty()) {
                    inMsg = true;
                }
            }
            result.put("message", msgBuilder.toString().stripTrailing());
            return result;
        }
    }

    // ─────────────────────────────────────────────
    // Repository
    // ─────────────────────────────────────────────

    static class Repository {
        final Path root;
        final Path rexDir;
        final Path objectsDir;
        final Path headsDir;
        final Path headFile;
        final Path indexFile;

        Repository(String path) {
            root       = Path.of(path).toAbsolutePath().normalize();
            rexDir     = root.resolve(".rex");
            objectsDir = rexDir.resolve("objects");
            headsDir   = rexDir.resolve("refs/heads");
            headFile   = rexDir.resolve("HEAD");
            indexFile  = rexDir.resolve("index");
        }

        // ── Setup ──────────────────────────────────

        boolean init() throws IOException {
            if (Files.exists(rexDir)) return false;
            Files.createDirectories(objectsDir);
            Files.createDirectories(headsDir);
            Files.writeString(headFile, "ref: refs/heads/master\n");
            saveIndex(new LinkedHashMap<>());
            System.out.println("Initialized empty Rex repository in " + root);
            return true;
        }

        // ── Object management ──────────────────────

        private Path[] objectPaths(String hash) {
            Path dir  = objectsDir.resolve(hash.substring(0, 2));
            Path file = dir.resolve(hash.substring(2));
            return new Path[]{dir, file};
        }

        String storeObject(GitObject obj) throws IOException {
            String hash = obj.hash();
            Path[] paths = objectPaths(hash);
            if (!Files.exists(paths[1])) {
                Files.createDirectories(paths[0]);
                Files.write(paths[1], obj.serialize());
            }
            return hash;
        }

        GitObject readObject(String hash) throws IOException {
            Path file = objectPaths(hash)[1];
            return GitObject.deserialize(Files.readAllBytes(file));
        }

        // ── Index management ───────────────────────

        Map<String, String> loadIndex() throws IOException {
            if (!Files.exists(indexFile)) return new LinkedHashMap<>();
            try {
                return parseJsonObject(Files.readString(indexFile));
            } catch (Exception e) {
                return new LinkedHashMap<>();
            }
        }

        void saveIndex(Map<String, String> index) throws IOException {
            // Minimal JSON serialisation (no external deps)
            StringBuilder sb = new StringBuilder("{\n");
            boolean first = true;
            for (var entry : index.entrySet()) {
                if (!first) sb.append(",\n");
                sb.append("  \"").append(jsonEscape(entry.getKey()))
                  .append("\": \"").append(jsonEscape(entry.getValue())).append("\"");
                first = false;
            }
            sb.append("\n}");
            Files.writeString(indexFile, sb.toString());
        }

        // ── Add operations ─────────────────────────

        void addFile(String relPath) throws IOException {
            Path full = root.resolve(relPath);
            String hash = storeObject(new Blob(Files.readAllBytes(full)));
            Map<String, String> index = loadIndex();
            index.put(relPath, hash);
            saveIndex(index);
            System.out.println("Added " + relPath);
        }

        void addDirectory(String relPath) throws IOException {
            Path full = root.resolve(relPath);
            Map<String, String> index = loadIndex();
            int[] count = {0};
            try (Stream<Path> stream = Files.walk(full)) {
                stream.filter(Files::isRegularFile)
                      .filter(p -> !p.toString().contains(".rex"))
                      .forEach(f -> {
                          try {
                              String hash = storeObject(new Blob(Files.readAllBytes(f)));
                              index.put(root.relativize(f).toString().replace('\\', '/'), hash);
                              count[0]++;
                          } catch (IOException e) {
                              throw new UncheckedIOException(e);
                          }
                      });
            }
            saveIndex(index);
            System.out.println("Added " + count[0] + " files from " + relPath);
        }

        void addPath(String path) throws IOException {
            Path full = root.resolve(path);
            if (Files.isRegularFile(full))       addFile(path);
            else if (Files.isDirectory(full))    addDirectory(path);
        }

        // ── Tree creation ──────────────────────────

        String createTreeFromIndex() throws IOException {
            Map<String, String> index = loadIndex();
            if (index.isEmpty()) return storeObject(new Tree(new ArrayList<>()));

            // Build a nested map representing the directory structure
            Map<String, Object> root = new TreeMap<>();
            for (var entry : index.entrySet()) {
                String[] parts = entry.getKey().split("/");
                @SuppressWarnings("unchecked")
                Map<String, Object> cur = root;
                for (int i = 0; i < parts.length - 1; i++) {
                    cur = (Map<String, Object>) cur.computeIfAbsent(parts[i], k -> new TreeMap<>());
                }
                cur.put(parts[parts.length - 1], entry.getValue());
            }
            return buildTree(root);
        }

        @SuppressWarnings("unchecked")
        private String buildTree(Map<String, Object> dir) throws IOException {
            Tree tree = new Tree(new ArrayList<>());
            for (var entry : dir.entrySet()) {
                String name = entry.getKey();
                Object val  = entry.getValue();
                if (val instanceof String blobHash) {
                    tree.addEntry("100644", name, blobHash);
                } else {
                    String subHash = buildTree((Map<String, Object>) val);
                    tree.addEntry("40000", name, subHash);
                }
            }
            return storeObject(tree);
        }

        // ── Commit logic ───────────────────────────

        /** Returns {ref (nullable), parentHash (nullable)}. */
        private String[] readHeadRef() throws IOException {
            if (!Files.exists(headFile)) return new String[]{null, null};
            String text = Files.readString(headFile).strip();
            if (text.startsWith("ref: ")) {
                String ref = text.substring(5);
                Path refFile = rexDir.resolve(ref);
                String parent = Files.exists(refFile) ? Files.readString(refFile).strip() : null;
                return new String[]{ref, parent};
            }
            return new String[]{null, text};
        }

        String commit(String message, String author) throws IOException {
            String treeHash = createTreeFromIndex();
            String[] headRef = readHeadRef();
            String ref    = headRef[0];
            String parent = headRef[1];

            long now = Instant.now().getEpochSecond();
            // Timezone offset in ±HHmm format
            String tz = DateTimeFormatter.ofPattern("Z")
                            .format(java.time.ZonedDateTime.now());
            String authorLine = author + " " + now + " " + tz;

            List<String> lines = new ArrayList<>();
            lines.add("tree " + treeHash);
            if (parent != null && !parent.isEmpty()) lines.add("parent " + parent);
            lines.add("author " + authorLine);
            lines.add("committer " + authorLine);
            lines.add("");
            lines.add(message);

            Commit commitObj = new Commit(String.join("\n", lines).getBytes(StandardCharsets.UTF_8));
            String commitHash = storeObject(commitObj);

            if (ref != null) {
                Files.writeString(rexDir.resolve(ref), commitHash + "\n");
            } else {
                Files.writeString(headFile, commitHash + "\n");
            }
            System.out.println("[commit " + commitHash.substring(0, 7) + "] " + message);
            return commitHash;
        }

        // ── Checkout ───────────────────────────────

        void checkout(String name, boolean create) throws IOException {
            Path refFile = headsDir.resolve(name);
            String targetCommit = null;

            if (Files.exists(refFile)) {
                targetCommit = Files.readString(refFile).strip();
                Files.writeString(headFile, "ref: refs/heads/" + name + "\n");
                System.out.println("Switched to branch '" + name + "'");

            } else if (isHash(name)) {
                targetCommit = name;
                Files.writeString(headFile, name + "\n");
                System.out.println("Note: detached HEAD at " + name.substring(0, 7));

            } else if (create) {
                String[] headRef = readHeadRef();
                String parent = headRef[1];
                Files.writeString(refFile, (parent != null ? parent : "") + "\n");
                Files.writeString(headFile, "ref: refs/heads/" + name + "\n");
                System.out.println("Created and switched to new branch '" + name + "'");
                return;

            } else {
                throw new IllegalArgumentException("Unknown branch/commit: " + name);
            }

            // Clean working directory (keep .rex)
            cleanWorkDir();

            String treeHash = readCommitTree(targetCommit);
            restoreTree(treeHash, root);
            System.out.println("Checked out " + name);
        }

        private void cleanWorkDir() throws IOException {
            try (Stream<Path> entries = Files.list(root)) {
                for (Path item : entries.toList()) {
                    if (item.getFileName().toString().equals(".rex")) continue;
                    if (Files.isRegularFile(item)) {
                        Files.delete(item);
                    } else if (Files.isDirectory(item)) {
                        deleteDirectoryRecursively(item);
                    }
                }
            }
        }

        private void deleteDirectoryRecursively(Path dir) throws IOException {
            try (Stream<Path> walk = Files.walk(dir)) {
                List<Path> paths = walk.sorted(Comparator.reverseOrder()).toList();
                for (Path p : paths) Files.deleteIfExists(p);
            }
        }

        private String readCommitTree(String commitHash) throws IOException {
            GitObject obj = readObject(commitHash);
            for (String line : new String(obj.content, StandardCharsets.UTF_8).split("\n")) {
                if (line.startsWith("tree ")) return line.substring(5);
            }
            throw new IllegalStateException("No tree found in commit");
        }

        private void restoreTree(String treeHash, Path base) throws IOException {
            GitObject obj = readObject(treeHash);
            if (!(obj instanceof Tree tree))
                throw new IllegalStateException("Not a tree object: " + treeHash);
            for (String[] entry : tree.entries) {
                String mode = entry[0], name = entry[1], hash = entry[2];
                Path dest = base.resolve(name);
                if ("40000".equals(mode)) {
                    Files.createDirectories(dest);
                    restoreTree(hash, dest);
                } else {
                    Files.createDirectories(dest.getParent());
                    GitObject blob = readObject(hash);
                    Files.write(dest, blob.content);
                }
            }
        }

        // ── Log ────────────────────────────────────

        void log() throws IOException {
            String[] headRef = readHeadRef();
            String head = headRef[1];
            if (head == null || head.isEmpty()) {
                System.out.println("No commits yet.");
                return;
            }
            String current = head;
            while (current != null && !current.isEmpty()) {
                GitObject obj = readObject(current);
                if (!(obj instanceof Commit commit)) break;
                Map<String, String> data = commit.parse();

                System.out.println("commit " + current);
                String authorField = data.get("author");
                if (authorField != null) {
                    String[] parts = authorField.split("\\s+");
                    if (parts.length >= 2) {
                        // last two tokens are timestamp + tz
                        long ts = Long.parseLong(parts[parts.length - 2]);
                        String nameEmail = String.join(" ",
                            Arrays.copyOfRange(parts, 0, parts.length - 2));
                        String dateStr = DateTimeFormatter
                            .ofPattern("yyyy-MM-dd HH:mm:ss")
                            .withZone(ZoneOffset.UTC)
                            .format(Instant.ofEpochSecond(ts));
                        System.out.println("Author: " + nameEmail);
                        System.out.println("Date:   " + dateStr + " UTC");
                    }
                }
                System.out.println("\n    " + data.getOrDefault("message", "") + "\n");
                current = data.get("parent");
            }
        }

        // ── Helpers ────────────────────────────────

        private boolean isHash(String s) {
            if (s.length() < 6) return false;
            for (char c : s.toCharArray())
                if ("0123456789abcdef".indexOf(Character.toLowerCase(c)) == -1) return false;
            return true;
        }

        // Minimal JSON object parser (key→value string map, no nested objects).
        private static Map<String, String> parseJsonObject(String json) {
            Map<String, String> map = new LinkedHashMap<>();
            json = json.strip();
            if (json.startsWith("{")) json = json.substring(1);
            if (json.endsWith("}"))  json = json.substring(0, json.length() - 1);
            // Split on ","  – works for flat string-only JSON as produced by saveIndex
            String[] pairs = json.split(",(?=\\s*\")");
            for (String pair : pairs) {
                String[] kv = pair.strip().split("\"\\s*:\\s*\"");
                if (kv.length != 2) continue;
                String k = kv[0].replaceAll("^\"|\"$", "");
                String v = kv[1].replaceAll("\"$",     "");
                map.put(jsonUnescape(k), jsonUnescape(v));
            }
            return map;
        }

        private static String jsonEscape(String s) {
            return s.replace("\\", "\\\\").replace("\"", "\\\"");
        }

        private static String jsonUnescape(String s) {
            return s.replace("\\\"", "\"").replace("\\\\", "\\");
        }
    }

    // ─────────────────────────────────────────────
    // CLI entry point
    // ─────────────────────────────────────────────

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            printHelp();
            return;
        }

        Repository repo = new Repository(".");
        String cmd = args[0];

        switch (cmd) {
            case "init" -> repo.init();

            case "add" -> {
                if (args.length < 2) { System.err.println("Usage: rex add <path> [<path>...]"); return; }
                for (int i = 1; i < args.length; i++) repo.addPath(args[i]);
            }

            case "commit" -> {
                String message = null;
                String author  = "Rex User <user@rex.com>";
                for (int i = 1; i < args.length; i++) {
                    if (("-m".equals(args[i]) || "--message".equals(args[i])) && i + 1 < args.length)
                        message = args[++i];
                    else if ("--author".equals(args[i]) && i + 1 < args.length)
                        author = args[++i];
                }
                if (message == null) { System.err.println("commit requires -m <message>"); return; }
                repo.commit(message, author);
            }

            case "checkout" -> {
                if (args.length < 2) { System.err.println("Usage: rex checkout <name> [--create]"); return; }
                String name   = args[1];
                boolean create = args.length >= 3 && "--create".equals(args[2]);
                repo.checkout(name, create);
            }

            case "log" -> repo.log();

            default -> printHelp();
        }
    }

    private static void printHelp() {
        System.out.println("""
                Rex — Simple Git-like VCS
                
                Commands:
                  init
                  add <path> [<path> ...]
                  commit -m <message> [--author <"Name <email>">]
                  checkout <branch-or-hash> [--create]
                  log
                """);
    }
}
