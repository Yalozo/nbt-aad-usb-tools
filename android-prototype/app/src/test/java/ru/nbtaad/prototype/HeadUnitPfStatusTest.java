package ru.nbtaad.prototype;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;

public class HeadUnitPfStatusTest {
    @Rule public TemporaryFolder cache = new TemporaryFolder();

    private static final String EMBEDDED = "pf_aa 1.3 (built into nbt-aad): Android Auto";

    private static final class Reader implements HeadUnitInstaller.Transport {
        byte[] daemon = elf(EMBEDDED), helper = new byte[0];
        int checkCode;
        final List<String> commands = new ArrayList<>();

        @Override public byte[] run(String command, byte[] input, long timeout, int limit) throws Exception {
            commands.add(command);
            if (command.endsWith(" --pf-check") || command.endsWith(" --check")) {
                if (checkCode != 0) {
                    ByteArrayOutputStream output = new ByteArrayOutputStream();
                    output.write("PF check details".getBytes(StandardCharsets.UTF_8));
                    throw new SshReader.CommandFailure("PF check failed", checkCode, output, new ByteArrayOutputStream());
                }
                return "rules look good and are active".getBytes(StandardCharsets.UTF_8);
            }
            if (command.contains("then cat '/opt/aa/nbt-aad';")) return daemon;
            if (command.contains("then cat '/opt/aa/pf_aa';")) return helper;
            return new byte[0];
        }
    }

    @Test public void embeddedPfIsRecognizedAndCheckedThroughTheStatusEntryPoint() throws Exception {
        Reader reader = new Reader();
        List<String> logs = status(reader);
        assertTrue(logs.contains("pf_aa встроен в nbt-aad — версия 1.3"));
        assertTrue(logs.contains("Проверка PF пройдена."));
        assertTrue(reader.commands.contains("/opt/aa/nbt-aad --pf-check"));
        assertFalse(logs.stream().anyMatch(line -> line.contains("pf_aa отсутствует")));
        assertFalse(reader.commands.stream().anyMatch(line -> line.contains("--pf-install")));
    }

    @Test public void standaloneUtilityUsesItsOwnCheckCommand() throws Exception {
        Reader reader = new Reader();
        reader.helper = elf("pf_aa 1.1: Android Auto");
        List<String> logs = status(reader);
        assertTrue(logs.contains("pf_aa — 1.1"));
        assertTrue(reader.commands.contains("/opt/aa/pf_aa --check"));
        assertFalse(reader.commands.contains("/opt/aa/nbt-aad --pf-check"));
    }

    @Test public void pendingRulesAndIncompleteConfigurationAreReportedSeparately() throws Exception {
        Reader reader = new Reader();
        reader.checkCode = 9;
        assertTrue(status(reader).stream().anyMatch(line -> line.contains("Правила сейчас не активны")));
        reader.checkCode = 7;
        List<String> logs = status(reader);
        assertTrue(logs.stream().anyMatch(line -> line.contains("неполную настройку")));
        assertFalse(logs.contains("Проверка PF пройдена."));
    }

    @Test public void absentOrInvalidUtilityIsNeverExecuted() throws Exception {
        for (byte[] daemon : new byte[][]{elf("older daemon"), EMBEDDED.getBytes(StandardCharsets.UTF_8)}) {
            Reader reader = new Reader();
            reader.daemon = daemon;
            status(reader);
            assertFalse(reader.commands.stream().anyMatch(line -> line.endsWith(" --pf-check")));
        }
    }

    private List<String> status(Reader reader) throws Exception {
        List<String> logs = new ArrayList<>();
        new HeadUnitInstaller(reader, logs::add).status(new byte[0], new ReleaseStore(cache.getRoot()));
        return logs;
    }

    private static byte[] elf(String banner) {
        byte[] text = banner.getBytes(StandardCharsets.UTF_8);
        byte[] data = new byte[52 + text.length];
        System.arraycopy(new byte[]{0x7f, 'E', 'L', 'F', 1, 1, 1}, 0, data, 0, 7);
        data[16] = 3;
        data[18] = 40;
        data[40] = 52;
        System.arraycopy(text, 0, data, 52, text.length);
        return data;
    }
}
