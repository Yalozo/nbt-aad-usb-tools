package ru.nbtaad.prototype;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class HeadUnitEnv {
    static final String PATH = "/opt/aa/nbt-aad.env";
    static final String BACKUP = PATH + ".bak";
    private static final int MAX_FILE = 16 * 1024;
    // The iDrive launcher uses fgets(line, 160) and has 15 slots besides PATH.
    private static final int MAX_LINE_BYTES = 158;
    private static final int MAX_DIRECTIVES = 15;
    private static final Pattern ASSIGNMENT = Pattern.compile("([A-Za-z_][A-Za-z0-9_]*)=.*");
    private static final String CHECK_DIRECTORY =
        "[ -d /opt/aa ] && [ ! -L /opt/aa ] || { echo 'Нет каталога NBT-AAD /opt/aa.' >&2; exit 1; }\n";
    private static final String SAFE_FILE =
        "[ ! -L \"$env\" ] && { [ ! -e \"$env\" ] || [ -f \"$env\" ]; } "
        + "|| { echo 'nbt-aad.env должен быть обычным файлом.' >&2; exit 1; }\n";

    static final class Snapshot {
        final boolean exists;
        final byte[] bytes;

        Snapshot(boolean exists, byte[] bytes) {
            this.exists = exists;
            this.bytes = bytes;
        }

        String text() throws IOException {
            return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        }
    }

    static Snapshot read(HeadUnitInstaller.Transport session) throws Exception {
        byte[] data = session.run("env=" + PATH + "\n" + CHECK_DIRECTORY + SAFE_FILE
            + "if [ -f \"$env\" ]; then echo present; cat \"$env\"; else echo absent; fi", null, 15000, MAX_FILE + 8);
        byte[] present = "present\n".getBytes(StandardCharsets.US_ASCII);
        if (data.length >= present.length && Arrays.equals(present, Arrays.copyOf(data, present.length))) {
            return new Snapshot(true, Arrays.copyOfRange(data, present.length, data.length));
        }
        if (Arrays.equals(data, "absent\n".getBytes(StandardCharsets.US_ASCII))) {
            return new Snapshot(false, new byte[0]);
        }
        throw new IOException("ГУ вернуло неизвестный формат ответа при чтении nbt-aad.env.");
    }

    static byte[] validate(Snapshot original, String draft) throws IOException {
        String text = normalize(draft);
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_FILE) throw new IOException("Файл превышает 16 КиБ.");
        Set<String> existingOther = new HashSet<>(Arrays.asList(normalize(original.text()).split("\\n", -1)));
        Set<String> seen = new HashSet<>();
        int lineNumber = 0;
        for (String line : text.split("\\n", -1)) {
            lineNumber++;
            String prefix = "Строка " + lineNumber + ". ";
            if (line.getBytes(StandardCharsets.UTF_8).length > MAX_LINE_BYTES) {
                throw new IOException(prefix + "Длина превышает 158 байт.");
            }
            for (int i = 0; i < line.length(); i++) {
                char c = line.charAt(i);
                if ((c < 32 && c != '\t') || c == 127) {
                    throw new IOException(prefix + "Есть недопустимый управляющий символ.");
                }
            }
            if (line.trim().isEmpty() || line.startsWith("#")) continue;
            if (line.trim().startsWith("#") && !line.contains("=")) continue;
            Matcher assignment = ASSIGNMENT.matcher(line);
            if (!assignment.matches()) throw new IOException(prefix + "Введите КЛЮЧ=значение без пробелов перед ключом и перед знаком =.");
            String key = assignment.group(1);
            if (!key.startsWith("NBT_") && !existingOther.contains(line)) {
                throw new IOException(prefix + "Добавлять и изменять можно параметры NBT_.");
            }
            if (!seen.add(key)) throw new IOException(prefix + "Параметр " + key + " уже задан выше.");
            if (seen.size() > MAX_DIRECTIVES) {
                throw new IOException(prefix + "Плагин читает не более 15 параметров. Уберите лишний или закомментируйте его.");
            }
        }
        return bytes;
    }

    static String setFlag(String draft, String key, String value) {
        String text = normalize(draft);
        String[] lines = text.split("\\n", -1);
        StringBuilder result = new StringBuilder();
        boolean found = false;
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (i == lines.length - 1 && line.isEmpty()) break;
            if (line.startsWith(key + "=")) {
                if (!found && value != null) result.append(key).append('=').append(value).append('\n');
                found = true;
            } else {
                result.append(line).append('\n');
            }
        }
        if (!found && value != null) result.append(key).append('=').append(value).append('\n');
        return result.toString();
    }

    static String normalize(String text) { return text.replace("\r\n", "\n"); }

    static void save(HeadUnitInstaller.Transport session, Snapshot original, byte[] next, ReleaseStore.Log log)
            throws Exception {
        String token = UUID.randomUUID().toString().replace("-", "");
        String stage = "/var/opt/nbt-aad-env-" + token;
        String temporary = "/opt/aa/.nbt-aad-env-" + token;
        session.run("umask 077; mkdir " + stage, null, 15000, 4096);
        try {
            upload(session, stage + "/next", next);
            if (original.exists) upload(session, stage + "/previous", original.bytes);
            session.run(commit(stage, temporary, original.exists), null, 30000, 4096);
            Snapshot saved = read(session);
            if (!saved.exists || !Arrays.equals(saved.bytes, next)) {
                throw new IOException("Содержимое nbt-aad.env после сохранения не совпало. Проверьте файл на ГУ.");
            }
        } finally {
            try { session.run("rm -rf " + stage, null, 15000, 4096); }
            catch (Exception e) { log.write("Временный каталог на ГУ: " + stage); }
        }
    }

    private static void upload(HeadUnitInstaller.Transport session, String path, byte[] data) throws Exception {
        session.run("umask 077; cat > " + path, data, 15000, 4096);
        if (!Arrays.equals(session.run("cat " + path, null, 15000, MAX_FILE), data)) {
            throw new IOException("Переданный файл не совпал с исходным.");
        }
    }

    private static String commit(String stage, String temporary, boolean exists) {
        String changed = " { echo 'Файл изменился на ГУ. Откройте редактор заново.' >&2; exit 1; }\n";
        String compare = exists
            ? "[ -f \"$env\" ] && diff \"$env\" \"$src/previous\" >/dev/null 2>&1 ||" + changed
            : "[ ! -e \"$env\" ] ||" + changed;
        return "set -eu\numask 077\nenv=" + PATH + "\nbak=" + BACKUP + "\nsrc=" + stage
            + "\ntmp=" + temporary + "\nlock=/var/opt/nbt-aad-usb-tools.lock\n"
            + "mkdir \"$lock\" 2>/dev/null || { echo 'ГУ занято другой операцией NBT-AAD.' >&2; exit 1; }\n"
            + "created=0\n"
            + "cleanup() {\n"
            + "  if [ \"$created\" = 1 ]; then rm -rf \"$tmp\"; fi\n"
            + "  if [ -d \"$lock\" ] && [ ! -L \"$lock\" ] && [ \"$(cat \"$lock/pid\" 2>/dev/null)\" = \"$$\" ]; then rm -rf \"$lock\"; fi\n"
            + "}\n"
            + "trap cleanup 0\ntrap 'exit 129' 1\ntrap 'exit 130' 2\ntrap 'exit 143' 15\n"
            + "echo \"$$\" > \"$lock/pid\"\n"
            + CHECK_DIRECTORY + SAFE_FILE + compare
            + "[ ! -L \"$bak\" ] && { [ ! -e \"$bak\" ] || [ -f \"$bak\" ]; } "
            + "|| { echo 'Резервная копия должна быть обычным файлом.' >&2; exit 1; }\n"
            + "mount -o remount rw /fs/sda0\n"
            + "mkdir \"$tmp\"\ncreated=1\n"
            + (exists ? "cp -p \"$env\" \"$tmp/next\"\ncat \"$src/next\" > \"$tmp/next\"\n"
                : "cp \"$src/next\" \"$tmp/next\"\nchmod 644 \"$tmp/next\"\n")
            + "diff \"$src/next\" \"$tmp/next\" >/dev/null 2>&1\n"
            + (exists ? "cp \"$src/previous\" \"$tmp/previous\"\nchmod 600 \"$tmp/previous\"\n"
                + "diff \"$src/previous\" \"$tmp/previous\" >/dev/null 2>&1\n" : "")
            + SAFE_FILE + compare
            + (exists ? "mv \"$tmp/previous\" \"$bak\"\n" : "")
            + "mv \"$tmp/next\" \"$env\"\n";
    }
}
