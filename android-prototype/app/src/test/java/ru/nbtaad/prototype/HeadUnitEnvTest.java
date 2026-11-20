package ru.nbtaad.prototype;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.*;

public class HeadUnitEnvTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();

    @Test public void presetsReplaceOnlyTheirOwnDirectiveAndPreserveComments() throws Exception {
        String before = "# Test\r\nNBT_VID_DPI=152\r\n#NBT_BT_TRACE=0\r\nNBT_BT_TRACE=0\r\n";
        String after = HeadUnitEnv.setFlag(before, "NBT_BT_TRACE", "1");
        assertEquals("# Test\nNBT_VID_DPI=152\n#NBT_BT_TRACE=0\nNBT_BT_TRACE=1\n", after);
        assertEquals("# Test\nNBT_VID_DPI=152\n#NBT_BT_TRACE=0\n", HeadUnitEnv.setFlag(after, "NBT_BT_TRACE", null));
        assertArrayEquals(utf8(after), HeadUnitEnv.validate(new HeadUnitEnv.Snapshot(true, utf8(before)), after));
    }

    @Test public void parserLimitsAndNonNbtVariablesAreChecked() throws Exception {
        HeadUnitEnv.Snapshot original = new HeadUnitEnv.Snapshot(true, utf8("OTHER=old\n"));
        assertArrayEquals(utf8("OTHER=old\nNBT_RAM_LOG=1\n"),
            HeadUnitEnv.validate(original, "OTHER=old\nNBT_RAM_LOG=1\n"));
        reject(original, "OTHER=changed\n");
        reject(original, "LD_PRELOAD=/tmp/lib.so\n");
        reject(original, "NBT_BT_TRACE=1\nNBT_BT_TRACE=0\n");
        StringBuilder lines = new StringBuilder();
        for (int i = 0; i < 16; i++) lines.append("NBT_FLAG_").append(i).append("=1\n");
        reject(original, lines.toString());
        StringBuilder longLine = new StringBuilder("NBT_LABEL=");
        while (longLine.length() < 90) longLine.append('я');
        reject(original, longLine.toString());
    }

    @Test public void saveCreatesAFileAndKeepsExactPreviousBytesOnUpdate() throws Exception {
        FakeHeadUnit hu = new FakeHeadUnit(temp.newFolder());
        HeadUnitEnv.Snapshot original = HeadUnitEnv.read(hu);
        assertFalse(original.exists);
        byte[] first = utf8("# Comments stay\nNBT_RAM_LOG=1\n");
        HeadUnitEnv.save(hu, original, first, line -> fail(line));
        assertArrayEquals(first, Files.readAllBytes(hu.env));
        assertFalse(Files.exists(hu.backup));

        byte[] second = utf8("NBT_MOST_LOG=1\n");
        HeadUnitEnv.save(hu, HeadUnitEnv.read(hu), second, line -> fail(line));
        assertArrayEquals(second, Files.readAllBytes(hu.env));
        assertArrayEquals(first, Files.readAllBytes(hu.backup));
        assertFalse(Files.exists(hu.lock));
    }

    @Test public void concurrentEditsAndInstallerLockPreventOverwrite() throws Exception {
        FakeHeadUnit hu = new FakeHeadUnit(temp.newFolder());
        Files.write(hu.env, utf8("NBT_RAM_LOG=0\n"));
        HeadUnitEnv.Snapshot original = HeadUnitEnv.read(hu);
        Files.write(hu.env, utf8("NBT_RAM_LOG=1\n"));
        try {
            HeadUnitEnv.save(hu, original, utf8("NBT_BT_TRACE=1\n"), line -> fail(line));
            fail("Expected stale snapshot to be rejected");
        } catch (IOException expected) {
            assertArrayEquals(utf8("NBT_RAM_LOG=1\n"), Files.readAllBytes(hu.env));
            assertFalse(Files.exists(hu.backup));
            assertFalse(Files.exists(hu.lock));
        }

        Files.createDirectory(hu.lock);
        try {
            HeadUnitEnv.save(hu, HeadUnitEnv.read(hu), utf8("NBT_BT_TRACE=1\n"), line -> fail(line));
            fail("Expected active installer lock to be respected");
        } catch (IOException expected) {
            assertTrue(Files.isDirectory(hu.lock));
            assertArrayEquals(utf8("NBT_RAM_LOG=1\n"), Files.readAllBytes(hu.env));
        }
    }

    private static final class FakeHeadUnit implements HeadUnitInstaller.Transport {
        final Path aa, staging, env, backup, lock;

        FakeHeadUnit(File directory) throws IOException {
            aa = Files.createDirectory(directory.toPath().resolve("aa"));
            staging = Files.createDirectory(directory.toPath().resolve("staging"));
            env = aa.resolve("nbt-aad.env");
            backup = aa.resolve("nbt-aad.env.bak");
            lock = staging.resolve("nbt-aad-usb-tools.lock");
        }

        @Override public byte[] run(String command, byte[] input, long timeout, int limit) throws Exception {
            String local = command.replace("/opt/aa", aa.toString()).replace("/var/opt", staging.toString())
                .replace("mount -o remount rw /fs/sda0", "true");
            Process process = new ProcessBuilder("/bin/sh", "-c", local).redirectErrorStream(true).start();
            try (java.io.OutputStream stdin = process.getOutputStream()) {
                if (input != null) stdin.write(input);
            }
            byte[] output;
            try (java.io.InputStream stdout = process.getInputStream()) {
                output = ReleaseStore.readBounded(stdout, limit);
            }
            if (!process.waitFor(5, TimeUnit.SECONDS) || process.exitValue() != 0) {
                process.destroyForcibly();
                throw new IOException(new String(output, StandardCharsets.UTF_8));
            }
            return output;
        }
    }

    private static void reject(HeadUnitEnv.Snapshot original, String text) {
        try { HeadUnitEnv.validate(original, text); fail("Expected invalid env"); }
        catch (IOException expected) {}
    }

    private static byte[] utf8(String text) { return text.getBytes(StandardCharsets.UTF_8); }
}
