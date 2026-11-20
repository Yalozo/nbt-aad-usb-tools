package ru.nbtaad.prototype;

import org.junit.Test;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import static org.junit.Assert.*;

public class ReleaseStoreTest {
    @Test public void acceptsCompleteOfficialLayoutAndRejectsUnexpectedFiles() throws Exception {
        Map<String, byte[]> files = validArchive();
        assertEquals(5, ReleaseStore.unpackPlugin(zip(files)).size());
        files.put("release/extra.sh", new byte[]{1});
        expectRejection(zip(files));
    }

    @Test public void rejectsTraversalAndSplitPluginDirectories() throws Exception {
        Map<String, byte[]> files = validArchive();
        files.put("../notes.md", new byte[]{1});
        expectRejection(zip(files));
        files.remove("../notes.md");
        files.put("elsewhere/NbtAad.xml", files.remove("release/NbtAad.xml"));
        expectRejection(zip(files));
    }

    @Test public void rejectsInvalidExecutableAndResourceArchive() throws Exception {
        Map<String, byte[]> files = validArchive();
        files.put("release/libNbtAad.so", new byte[]{1});
        expectRejection(zip(files));
        files = validArchive();
        files.put("release/NbtAad_ALL_Images.zip", new byte[]{1});
        expectRejection(zip(files));
    }

    private static Map<String, byte[]> validArchive() throws IOException {
        Map<String, byte[]> files = new LinkedHashMap<>();
        for (String name : ReleaseStore.PLUGIN) files.put("release/" + name, new byte[]{'x'});
        byte[] elf = new byte[52];
        System.arraycopy(new byte[]{0x7f, 'E', 'L', 'F', 1, 1, 1}, 0, elf, 0, 7);
        elf[16] = 3;
        elf[18] = 40;
        elf[40] = 52;
        files.put("release/libNbtAad.so", elf);
        byte[] resources = zip(java.util.Collections.singletonMap("resource", new byte[]{1}));
        files.put("release/NbtAad_ALL_Images.zip", resources);
        files.put("release/NbtAad_ALL_Texts.zip", resources);
        return files;
    }

    private static byte[] zip(Map<String, byte[]> files) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(out)) {
            for (Map.Entry<String, byte[]> file : files.entrySet()) {
                zip.putNextEntry(new ZipEntry(file.getKey()));
                zip.write(file.getValue());
                zip.closeEntry();
            }
        }
        return out.toByteArray();
    }

    private static void expectRejection(byte[] data) {
        try {
            ReleaseStore.unpackPlugin(data);
            fail("Expected invalid archive to be rejected");
        } catch (IOException expected) {}
    }
}
