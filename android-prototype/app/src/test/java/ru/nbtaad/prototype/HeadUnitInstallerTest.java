package ru.nbtaad.prototype;

import org.junit.Test;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static org.junit.Assert.*;

public class HeadUnitInstallerTest {
    private static final byte[] SCRIPT = "test script".getBytes(StandardCharsets.US_ASCII);

    private static final class FakeHeadUnit implements HeadUnitInstaller.Transport {
        final List<String> commands = new ArrayList<>();
        final Map<String, byte[]> uploaded = new LinkedHashMap<>();
        boolean corruptUpload, rejectValidation, rejectFinalCheck, missingSuccess;

        @Override public byte[] run(String command, byte[] input, long timeout, int limit) throws Exception {
            commands.add(command);
            if (command.startsWith("umask 077; cat > ")) {
                uploaded.put(command.substring("umask 077; cat > ".length()), input);
            } else if (command.startsWith("cat ")) {
                byte[] bytes = uploaded.get(command.substring(4));
                return corruptUpload ? new byte[]{0} : bytes;
            } else if (command.endsWith(" --dry-run")) {
                return utf8(rejectValidation ? "No confirmation." : "DRY RUN: validation passed. No remount.");
            } else if (command.startsWith("/bin/sh ")) {
                if (missingSuccess) return utf8("No confirmation.");
                return utf8(command.contains(" install ") ? "SUCCESS: install completed. Previous files: /backup"
                    : "SUCCESS: uninstall completed. Previous files: /backup");
            } else if (command.startsWith("test ") && rejectFinalCheck) {
                throw new IOException("Final check failed");
            }
            return new byte[0];
        }

        boolean changedFiles() {
            return commands.stream().anyMatch(cmd -> cmd.startsWith("/bin/sh ") && !cmd.endsWith(" --dry-run"));
        }
    }

    @Test public void uploadDryRunCommitAndCleanupAreOrdered() throws Exception {
        FakeHeadUnit hu = new FakeHeadUnit();
        Map<String, byte[]> files = new LinkedHashMap<>();
        files.put("nbt-aad", new byte[]{1});
        for (String name : ReleaseStore.PLUGIN) files.put("release/" + name, new byte[]{2});
        files.put("pf_aa", new byte[]{3});
        new HeadUnitInstaller(hu, line -> {}).change(HeadUnitInstaller.Action.INSTALL, SCRIPT, files);

        assertEquals(8, hu.uploaded.size());
        int dryRun = indexOf(hu.commands, " --dry-run");
        String dryRunCommand = hu.commands.get(dryRun);
        assertFalse(dryRunCommand.contains(" --pf-embedded"));
        assertEquals(dryRunCommand.substring(0, dryRunCommand.lastIndexOf(" --dry-run")), hu.commands.get(dryRun + 1));
        assertTrue(hu.commands.get(dryRun + 2).contains("test -x /opt/aa/pf_aa"));
        assertTrue(hu.commands.get(dryRun + 3).startsWith("rm -rf '/var/opt/nbt-aad-pc-"));
    }

    @Test public void daemonWithEmbeddedPfIsSelectedForBothInstallCommands() throws Exception {
        FakeHeadUnit hu = new FakeHeadUnit();
        Map<String, byte[]> files = new LinkedHashMap<>();
        files.put("nbt-aad", utf8("pf_aa 1.3 (built into nbt-aad): Android Auto"));
        for (String name : ReleaseStore.PLUGIN) files.put("release/" + name, new byte[]{2});
        new HeadUnitInstaller(hu, line -> {}).change(HeadUnitInstaller.Action.INSTALL, SCRIPT, files);

        int dryRun = indexOf(hu.commands, " --pf-embedded --dry-run");
        assertTrue(hu.commands.get(dryRun + 1).endsWith(" --pf-embedded"));
    }

    @Test public void corruptedUploadAndUnconfirmedDryRunNeverChangeInstalledFiles() throws Exception {
        for (boolean corrupt : Arrays.asList(true, false)) {
            FakeHeadUnit hu = new FakeHeadUnit();
            hu.corruptUpload = corrupt;
            hu.rejectValidation = !corrupt;
            try {
                new HeadUnitInstaller(hu, line -> {}).change(HeadUnitInstaller.Action.UNINSTALL, SCRIPT, null);
                fail("Expected validation failure");
            } catch (IOException expected) {
                assertFalse(hu.changedFiles());
            }
        }
    }

    @Test public void stagedFilesRemainIfCommitOrResultCannotBeVerified() throws Exception {
        for (boolean missingMarker : Arrays.asList(true, false)) {
            FakeHeadUnit hu = new FakeHeadUnit();
            hu.missingSuccess = missingMarker;
            hu.rejectFinalCheck = !missingMarker;
            try {
                new HeadUnitInstaller(hu, line -> {}).change(HeadUnitInstaller.Action.PURGE, SCRIPT, null);
                fail("Expected operation failure");
            } catch (IOException expected) {
                assertTrue(hu.changedFiles());
                assertTrue(hu.commands.stream().noneMatch(cmd -> cmd.startsWith("rm -rf ")));
            }
        }
    }

    private static byte[] utf8(String text) { return text.getBytes(StandardCharsets.UTF_8); }

    private static int indexOf(List<String> commands, String text) {
        for (int i = 0; i < commands.size(); i++) if (commands.get(i).contains(text)) return i;
        throw new AssertionError(text);
    }
}
