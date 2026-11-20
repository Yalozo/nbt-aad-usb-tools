package ru.nbtaad.prototype;

import android.net.Network;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

final class ReleaseStore {
    static final int MAX_ASSET = 32 * 1024 * 1024;
    static final int MAX_UPLOAD = 16 * 1024 * 1024;
    static final List<String> PLUGIN = Arrays.asList("libNbtAad.so", "NbtAad.manifest", "NbtAad.xml",
        "NbtAad_ALL_Texts.zip", "NbtAad_ALL_Images.zip");
    private static final String REPO = "Sq00ky/Supra-iDrive6-Android-Auto";
    private static final String API = "https://api.github.com/repos/" + REPO + "/releases?per_page=100";
    private static final String ASSET_PREFIX = "https://github.com/" + REPO + "/releases/download/";
    private static final Pattern PREVIEW = Pattern.compile(
        "alpha|beta|preview|unstable|(?:^|[\\W_])(?:rc\\d*|test(?:ing)?)(?:$|[\\W_])", Pattern.CASE_INSENSITIVE);

    interface Log { void write(String text); }

    static final class Asset {
        final String name, url, digest;
        final int size;

        Asset(JSONObject json) throws Exception {
            name = json.getString("name");
            url = json.getString("browser_download_url");
            digest = json.getString("digest");
            size = json.getInt("size");
            if (!url.startsWith(ASSET_PREFIX) || !digest.matches("sha256:[0-9a-f]{64}")
                || size <= 0 || size > MAX_ASSET) {
                throw new IOException("Недопустимые данные файла релиза " + name);
            }
        }

        byte[] verify(byte[] bytes) throws Exception {
            if (bytes.length != size || !digest.equals("sha256:" + sha256(bytes))) {
                throw new IOException("Размер или SHA-256 не совпал для " + name);
            }
            return bytes;
        }
    }

    static final class Release {
        final JSONObject json;
        final String id, tag, title, date;
        final boolean preview;
        final List<Asset> assets;

        Release(JSONObject json) throws Exception {
            this.json = json;
            tag = json.getString("tag_name");
            title = json.isNull("name") || json.optString("name").isEmpty() ? tag : json.getString("name");
            date = json.getString("published_at");
            if (tag.isEmpty() || !date.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) {
                throw new IOException("Не удалось определить тег или дату релиза.");
            }
            preview = json.optBoolean("prerelease") || PREVIEW.matcher(tag + " " + title).find();
            JSONArray entries = json.getJSONArray("assets");
            Asset daemon = null, plugin = null, helper = null;
            for (int i = 0; i < entries.length(); i++) {
                JSONObject entry = entries.getJSONObject(i);
                String name = entry.getString("name");
                if ("nbt-aad".equals(name) && daemon == null) daemon = new Asset(entry);
                else if (name.matches("(?i)releases?\\.zip") && plugin == null) plugin = new Asset(entry);
                else if ("pf_aa".equals(name) && helper == null) helper = new Asset(entry);
                else if ("nbt-aad".equals(name) || name.matches("(?i)releases?\\.zip") || "pf_aa".equals(name)) {
                    throw new IOException("Повторяющийся файл релиза " + name);
                }
            }
            if (daemon == null || plugin == null) throw new IOException("Релиз не содержит полного комплекта.");
            assets = helper == null ? Arrays.asList(daemon, plugin) : Arrays.asList(daemon, plugin, helper);
            StringBuilder digests = new StringBuilder();
            for (Asset asset : assets) digests.append(asset.digest);
            id = sha256(digests.toString().getBytes(StandardCharsets.US_ASCII));
        }

        String label() {
            String name = title.equals(tag) ? title : title + " [" + tag + "]";
            return name + " · " + date.substring(0, 10);
        }
    }

    private final File directory;
    ReleaseStore(File directory) { this.directory = directory; }

    static List<Release> online(Network network) throws Exception {
        JSONArray entries = new JSONArray(new String(fetch(network, API, 4 * 1024 * 1024),
            StandardCharsets.UTF_8));
        List<Release> releases = new ArrayList<>();
        for (int i = 0; i < entries.length(); i++) {
            JSONObject entry = entries.getJSONObject(i);
            if (entry.optBoolean("draft")) continue;
            try { releases.add(new Release(entry)); }
            catch (Exception ignored) { /* Only complete releases with published hashes are installable. */ }
        }
        Collections.sort(releases, (a, b) -> b.date.compareTo(a.date));
        if (releases.isEmpty()) throw new IOException("GitHub не вернул подходящих релизов с SHA-256.");
        return releases;
    }

    List<Release> local(Log log) {
        List<Release> releases = new ArrayList<>();
        File[] dirs = directory.listFiles();
        if (dirs == null) return releases;
        for (File dir : dirs) {
            if (!dir.getName().matches("[0-9a-f]{64}") || !dir.isDirectory()) continue;
            try {
                Release release = new Release(new JSONObject(new String(readFile(new File(dir, "release.json"),
                    4 * 1024 * 1024), StandardCharsets.UTF_8)));
                if (!dir.getName().equals(release.id)) throw new IOException("Некорректный идентификатор.");
                for (Asset asset : release.assets) asset.verify(readFile(new File(dir, asset.name), asset.size));
                releases.add(release);
            } catch (Exception e) {
                log.write("Пропущен повреждённый комплект " + dir.getName().substring(0, 12));
            }
        }
        Collections.sort(releases, (a, b) -> b.date.compareTo(a.date));
        return releases;
    }

    Map<String, byte[]> load(Release release, Network internet, Log log) throws Exception {
        File target = new File(directory, release.id);
        if (target.isDirectory()) {
            try { return readPackage(release, target); }
            catch (Exception e) {
                if (internet == null) throw new IOException("Сохранённый комплект повреждён. Скачайте его заново.", e);
                log.write("Сохранённый комплект повреждён. Загружаю заново.");
            }
        }
        if (internet == null) throw new IOException("Для скачивания релиза нужен интернет через Wi-Fi или мобильную сеть.");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Не удалось создать каталог комплектов.");
        File incoming = new File(directory, "download-" + UUID.randomUUID());
        if (!incoming.mkdir()) throw new IOException("Не удалось создать каталог загрузки.");
        File old = null;
        try {
            for (Asset asset : release.assets) {
                log.write("Скачиваю " + asset.name);
                write(new File(incoming, asset.name), asset.verify(fetch(internet, asset.url, asset.size)));
            }
            Map<String, byte[]> payload = readPackage(release, incoming);
            write(new File(incoming, "release.json"), release.json.toString().getBytes(StandardCharsets.UTF_8));
            if (target.exists()) {
                old = new File(directory, "invalid-" + UUID.randomUUID());
                if (!target.renameTo(old)) throw new IOException("Не удалось заменить повреждённый комплект.");
            }
            if (!incoming.renameTo(target)) {
                if (old != null) old.renameTo(target);
                throw new IOException("Не удалось сохранить комплект.");
            }
            if (old != null) removeTemporary(old);
            log.write("Комплект проверен и сохранён.");
            return payload;
        } finally {
            removeTemporary(incoming);
        }
    }

    Map<String, byte[]> readCached(Release release) throws Exception {
        return readPackage(release, new File(directory, release.id));
    }

    private static Map<String, byte[]> readPackage(Release release, File path) throws Exception {
        Asset daemon = release.assets.get(0), plugin = release.assets.get(1);
        byte[] binary = daemon.verify(readFile(new File(path, daemon.name), daemon.size));
        validateElf(binary);
        Map<String, byte[]> files = new LinkedHashMap<>();
        files.put("nbt-aad", binary);
        files.putAll(unpackPlugin(plugin.verify(readFile(new File(path, plugin.name), plugin.size))));
        if (release.assets.size() == 3) {
            Asset helper = release.assets.get(2);
            byte[] data = helper.verify(readFile(new File(path, helper.name), helper.size));
            validateElf(data);
            files.put("pf_aa", data);
        }
        int total = 0;
        for (byte[] bytes : files.values()) total += bytes.length;
        if (total > MAX_UPLOAD) throw new IOException("Комплект превышает 16 МиБ.");
        return files;
    }

    static Map<String, byte[]> unpackPlugin(byte[] data) throws IOException {
        Map<String, byte[]> found = new LinkedHashMap<>();
        String parent = null;
        int entries = 0, budget = MAX_UPLOAD;
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(data))) {
            for (ZipEntry entry; (entry = zip.getNextEntry()) != null; ) {
                String name = entry.getName();
                if (++entries > 100 || name.startsWith("/") || name.contains("\\") || name.contains(":")
                    || Arrays.asList(name.split("/")).contains("..")) throw new IOException("Некорректный путь в ZIP.");
                if (entry.isDirectory()) {
                    if (zip.read() != -1) throw new IOException("Данные в записи каталога ZIP.");
                    continue;
                }
                int slash = name.lastIndexOf('/');
                String filename = name.substring(slash + 1);
                boolean expected = PLUGIN.contains(filename);
                String lower = filename.toLowerCase(Locale.ROOT);
                if (!expected && !lower.endsWith(".md") && !lower.endsWith(".txt")) {
                    throw new IOException("Неизвестный файл плагина " + filename);
                }
                byte[] bytes = readBounded(zip, budget);
                budget -= bytes.length;
                if (!expected) continue;
                String folder = name.substring(0, slash + 1);
                if (parent == null) parent = folder;
                if (!parent.equals(folder) || bytes.length == 0 || found.put("release/" + filename, bytes) != null) {
                    throw new IOException("Плагин содержит повторяющиеся, разнесённые или пустые файлы.");
                }
            }
        }
        if (found.size() != PLUGIN.size()) throw new IOException("Неполный комплект плагина.");
        validateElf(found.get("release/libNbtAad.so"));
        for (String name : PLUGIN.subList(1, 3)) {
            if (new String(found.get("release/" + name), StandardCharsets.UTF_8).trim().isEmpty()) {
                throw new IOException("Пустой файл " + name);
            }
        }
        for (String name : PLUGIN.subList(3, 5)) validateResourceZip(found.get("release/" + name));
        return found;
    }

    private static void validateResourceZip(byte[] bytes) throws IOException {
        int total = 0, entries = 0;
        byte[] buffer = new byte[8192];
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            while (zip.getNextEntry() != null) {
                if (++entries > 4096) throw new IOException("Слишком много ресурсов в ZIP.");
                for (int n; (n = zip.read(buffer)) != -1; ) {
                    total += n;
                    if (total > MAX_ASSET) throw new IOException("Архив ресурсов слишком большой.");
                }
            }
        }
        if (entries == 0) throw new IOException("Повреждён или пуст ZIP ресурсов.");
    }

    static void validateElf(byte[] data) throws IOException {
        if (data.length < 52 || data[0] != 0x7f || data[1] != 'E' || data[2] != 'L' || data[3] != 'F'
            || data[4] != 1 || data[5] != 1 || data[6] != 1 || (data[16] != 2 && data[16] != 3)
            || data[17] != 0 || data[18] != 40 || data[19] != 0 || data[40] != 52 || data[41] != 0) {
            throw new IOException("Ожидался ARM ELF32 для NBT Evo.");
        }
    }

    static String sha256(byte[] bytes) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder hex = new StringBuilder(64);
        for (byte b : digest) {
            hex.append(Character.forDigit((b >>> 4) & 15, 16));
            hex.append(Character.forDigit(b & 15, 16));
        }
        return hex.toString();
    }

    private static byte[] fetch(Network network, String address, int max) throws Exception {
        URL url = new URL(address);
        for (int attempt = 0; attempt < 5; attempt++) {
            if (!"https".equals(url.getProtocol()) || url.getUserInfo() != null
                || !Arrays.asList("api.github.com", "github.com", "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com").contains(url.getHost())) {
                throw new IOException("Недопустимый адрес скачивания.");
            }
            HttpURLConnection connection = (HttpURLConnection) network.openConnection(url);
            try {
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(40000);
                connection.setRequestProperty("User-Agent", "NBT-AAD-Android/1.0");
                connection.setRequestProperty("Accept", url.getHost().equals("api.github.com")
                    ? "application/vnd.github+json" : "application/octet-stream");
                int code = connection.getResponseCode();
                if (code >= 300 && code < 400 && connection.getHeaderField("Location") != null) {
                    url = new URL(url, connection.getHeaderField("Location"));
                    continue;
                }
                if (code != 200) throw new IOException("GitHub вернул HTTP " + code);
                try (InputStream in = connection.getInputStream()) { return readBounded(in, max); }
            } finally {
                connection.disconnect();
            }
        }
        throw new IOException("Слишком много перенаправлений при скачивании.");
    }

    static byte[] readBounded(InputStream in, int max) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        for (int n; (n = in.read(buffer)) != -1; ) {
            if (n > max - out.size()) throw new IOException("Размер данных превышает ограничение.");
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }

    private static byte[] readFile(File file, int limit) throws IOException {
        try (InputStream in = new java.io.FileInputStream(file)) { return readBounded(in, limit); }
    }

    private static void write(File file, byte[] data) throws IOException {
        try (FileOutputStream out = new FileOutputStream(file)) { out.write(data); }
    }

    private static void removeTemporary(File directory) {
        File[] files = directory.listFiles();
        if (files != null) for (File file : files) file.delete();
        directory.delete();
    }
}
