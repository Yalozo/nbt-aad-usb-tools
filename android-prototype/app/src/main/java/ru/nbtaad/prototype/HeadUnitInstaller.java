package ru.nbtaad.prototype;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class HeadUnitInstaller {
    enum Action { FIRMWARE, STATUS, INSTALL, UNINSTALL, PURGE, RESTART }

    interface Transport {
        byte[] run(String command, byte[] input, long timeout, int limit) throws Exception;
    }

    private static final String DAEMON = "/opt/aa/nbt-aad";
    private static final String HELPER = "/opt/aa/pf_aa";
    private static final String PLUGIN = "/opt/hmi/ID5/data/ro/Plugins/NbtAad";
    private static final String PF_VERSION = "pf_aa ([0-9]+(?:\\.[0-9]+)+):";
    private static final String EMBEDDED_PF_VERSION = "pf_aa ([0-9]+(?:\\.[0-9]+)+) \\(built into nbt-aad\\): Android Auto";
    private final Transport session;
    private final ReleaseStore.Log log;

    HeadUnitInstaller(Transport session, ReleaseStore.Log log) {
        this.session = session;
        this.log = log;
    }

    void change(Action action, byte[] script, Map<String, byte[]> payload) throws Exception {
        if (action != Action.INSTALL && action != Action.UNINSTALL && action != Action.PURGE) {
            throw new IllegalArgumentException("Неверное действие.");
        }
        boolean install = action == Action.INSTALL;
        boolean purge = action == Action.PURGE;
        Map<String, byte[]> files = new LinkedHashMap<>();
        files.put("nbt-aad.sh", script);
        if (install) {
            if (payload == null || !payload.containsKey("nbt-aad")) throw new IOException("Комплект не выбран.");
            for (String name : ReleaseStore.PLUGIN) {
                if (!payload.containsKey("release/" + name)) throw new IOException("Комплект неполный.");
            }
            files.put("nbt-aad", payload.get("nbt-aad"));
            for (String name : ReleaseStore.PLUGIN) files.put("release/" + name, payload.get("release/" + name));
            if (payload.containsKey("pf_aa")) files.put("pf_aa", payload.get("pf_aa"));
        }
        long total = 0;
        for (byte[] bytes : files.values()) total += bytes.length;
        if (total > ReleaseStore.MAX_UPLOAD) throw new IOException("Комплект и скрипт превышают 16 МиБ.");

        String stage = "/var/opt/nbt-aad-pc-" + UUID.randomUUID().toString().replace("-", "");
        String path = SshReader.quote(stage);
        boolean started = false;
        session.run("umask 077; mkdir " + path, null, 15000, 4096);
        try {
            if (install) session.run("umask 077; mkdir " + SshReader.quote(stage + "/release"), null, 15000, 4096);
            int number = 0;
            for (Map.Entry<String, byte[]> file : files.entrySet()) {
                log.write("Передаю и проверяю " + file.getKey() + " (" + ++number + "/" + files.size() + ")");
                String target = SshReader.quote(stage + "/" + file.getKey());
                byte[] bytes = file.getValue();
                session.run("umask 077; cat > " + target, bytes, 120000, 4096);
                byte[] actual = session.run("cat " + target, null, 120000, bytes.length + 4096);
                if (!ReleaseStore.sha256(actual).equals(ReleaseStore.sha256(bytes))) {
                    throw new IOException("SHA-256 переданного файла не совпал. " + file.getKey());
                }
            }
            String verb = install ? "install" : "uninstall";
            String command = "/bin/sh " + SshReader.quote(stage + "/nbt-aad.sh") + " " + verb + " --source " + path;
            if (install && !files.containsKey("pf_aa") && new String(files.get("nbt-aad"), StandardCharsets.ISO_8859_1)
                .contains("(built into nbt-aad): Android Auto")) command += " --pf-embedded";
            if (!install) command += purge ? " --purge" : " --keep-config";
            log.write("Проверочный запуск установщика.");
            String validation = runLogged(command + " --dry-run", null, 120000);
            if (!hasMarker(validation, "DRY RUN: validation passed.")
                && !(!install && hasMarker(validation, "Already uninstalled."))) {
                throw new IOException("Установщик не подтвердил готовность.");
            }
            log.write("Выполняю операцию. Оставьте зажигание и ENET включёнными.");
            started = true;
            String result = runLogged(command, null, 240000);
            if (!hasMarker(result, "SUCCESS: " + verb + " completed.")
                && !(!install && hasMarker(result, "Already uninstalled."))) {
                throw new IOException("Установщик не подтвердил завершение операции.");
            }
            String verify;
            if (install) {
                verify = "test -s " + DAEMON + " && test -s " + PLUGIN + "/libNbtAad.so";
                if (files.containsKey("pf_aa")) verify += " && test -s " + HELPER + " && test -x " + HELPER;
            } else {
                verify = "test ! -e " + DAEMON + " && test ! -e " + PLUGIN + " && test ! -e " + HELPER;
                if (purge) verify += " && test ! -e /opt/aa && test ! -e /var/opt/aa"
                    + " && test ! -e /opt/nbt-aad-usb-tools-backups && test ! -e /var/opt/nbt-aad-usb-tools-backups";
            }
            session.run(verify, null, 15000, 4096);
        } catch (Exception e) {
            if (!started) log.write("Изменение установленных файлов ещё не начиналось.");
            else if (purge) log.write("Полная очистка не подтверждена. Часть файлов могла быть уже удалена.");
            log.write("Временный каталог операции на hu-omap: " + stage);
            throw e;
        }
        try { session.run("rm -rf " + path, null, 30000, 4096); }
        catch (Exception e) { log.write("Проверьте временный каталог " + stage + ". " + e.getMessage()); }
        log.write(install ? "Установка подтверждена." : purge ? "Очистка подтверждена." : "Удаление подтверждено. Настройки сохранены.");
    }

    void status(byte[] script, ReleaseStore releases) throws Exception {
        runLogged("/bin/sh -s -- status", script, 30000);
        Map<String, String> digests = new LinkedHashMap<>();
        Map<String, String> paths = new LinkedHashMap<>();
        paths.put("nbt-aad", DAEMON);
        for (String name : ReleaseStore.PLUGIN) paths.put("release/" + name, PLUGIN + "/" + name);
        paths.put("pf_aa", HELPER);
        byte[] daemon = new byte[0], helper = new byte[0];
        for (Map.Entry<String, String> entry : paths.entrySet()) {
            String path = SshReader.quote(entry.getValue());
            byte[] data = session.run("if test -f " + path + " && test ! -L " + path + "; then cat " + path + "; fi",
                null, 120000, ReleaseStore.MAX_ASSET);
            if (data.length != 0) digests.put(entry.getKey(), ReleaseStore.sha256(data));
            switch (entry.getKey()) {
                case "nbt-aad":
                    daemon = data;
                    logVersion("NBT-AAD", data, "Build: (v[0-9]+(?:\\.[0-9]+){1,3}[A-Za-z0-9_.-]*)(?=[ \\x00(])");
                    break;
                case "release/libNbtAad.so":
                    logVersion("Плагин", data, "(?<![A-Za-z0-9_-])(rhmiapp-[0-9]+[A-Za-z0-9_.-]*)(?=[ \\x00(])");
                    break;
                case "pf_aa":
                    helper = data;
                    break;
                default: break;
            }
        }
        reportPfStatus(daemon, helper);
        List<ReleaseStore.Release> local = releases.local(log);
        String match = null;
        for (ReleaseStore.Release release : local) {
            Map<String, byte[]> files;
            try { files = releases.readCached(release); }
            catch (Exception e) { continue; }
            if (files.size() != digests.size()) continue;
            boolean identical = true;
            for (Map.Entry<String, byte[]> entry : files.entrySet()) {
                if (!ReleaseStore.sha256(entry.getValue()).equals(digests.get(entry.getKey()))) {
                    identical = false;
                    break;
                }
            }
            if (identical) { match = release.label(); break; }
        }
        if (match != null) log.write("Совпадение всех файлов по SHA-256: " + match);
        else if (!digests.isEmpty()) log.write("Точного совпадения со скачанными комплектами нет.");
    }

    private void logVersion(String name, byte[] data, String pattern) {
        if (data.length == 0) { log.write(name + " отсутствует."); return; }
        try { ReleaseStore.validateElf(data); }
        catch (IOException e) { log.write(name + " не является ARM ELF32."); return; }
        String version = findVersion(data, pattern);
        log.write(name + " — " + (version == null ? "версия не определена" : version));
    }

    private static String findVersion(byte[] data, String pattern) {
        Matcher versions = Pattern.compile(pattern).matcher(new String(data, StandardCharsets.ISO_8859_1));
        String version = null;
        while (versions.find()) {
            String next = versions.group(1);
            if (next.length() > 100) continue;
            if (version != null && !version.equals(next)) return null;
            version = next;
        }
        return version;
    }

    void reportPfStatus(byte[] daemon, byte[] helper) throws Exception {
        boolean embedded = helper.length == 0;
        byte[] tool = embedded ? daemon : helper;
        String version = findVersion(tool, embedded ? EMBEDDED_PF_VERSION : PF_VERSION);
        if (tool.length == 0 || (embedded && version == null)) {
            log.write("PF — утилита настройки в установленном комплекте не обнаружена.");
            return;
        }
        try { ReleaseStore.validateElf(tool); }
        catch (IOException e) {
            log.write("PF — файл утилиты не является ARM ELF32.");
            return;
        }
        if (version == null) {
            log.write("pf_aa — версия не определена, команда проверки недоступна.");
            return;
        }
        log.write(embedded ? "pf_aa встроен в nbt-aad — версия " + version : "pf_aa — " + version);
        String command = embedded ? DAEMON + " --pf-check" : HELPER + " --check";
        try {
            runLogged(command, null, 30000);
            log.write("Проверка PF пройдена.");
        } catch (SshReader.CommandFailure failure) {
            if (failure.code == 9) log.write("Файлы PF проверены. Правила сейчас не активны; требуется перезагрузка ГУ.");
            else if (failure.code == 7) log.write("Проверка PF выявила неполную настройку. Подробности выше.");
            else log.write("Проверка PF завершилась с кодом " + failure.code + ".");
        } catch (Exception e) {
            log.write("Не удалось проверить PF. " + e.getMessage());
        }
    }

    private String runLogged(String command, byte[] input, long timeout) throws Exception {
        try {
            String output = SshReader.text(session.run(command, input, timeout, 1024 * 1024));
            if (!output.trim().isEmpty()) log.write(output.trim());
            return output;
        } catch (SshReader.CommandFailure failure) {
            if (!failure.output.trim().isEmpty()) log.write(failure.output.trim());
            throw failure;
        }
    }

    private static boolean hasMarker(String output, String marker) {
        for (String line : output.split("\\r?\\n")) {
            if (line.equals(marker) || line.startsWith(marker + " ")) return true;
        }
        return false;
    }
}
