package ru.nbtaad.prototype;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import org.junit.Test;
import static org.junit.Assert.*;

public class HeadUnitDiagnosticsTest {
    private static final byte[] REQUEST = {0, 0, 0, 4, 0, 1, (byte) 0xf4, 0x63, 0x11, 1};

    private static class DiagnosticSocket extends Socket {
        final InputStream input;
        final ByteArrayOutputStream output = new ByteArrayOutputStream();

        DiagnosticSocket(InputStream input) { this.input = input; }
        @Override public InputStream getInputStream() { return input; }
        @Override public OutputStream getOutputStream() { return output; }
        @Override public void setSoTimeout(int timeout) { assertTrue(timeout > 0 && timeout <= 10000); }
    }

    @Test public void exactResetIsSentOnceAndOnlyHeadUnitPositiveResponseConfirmsIt() throws Exception {
        byte[] stream = join(frame(2, 0xf4, 0x63, 0x11, 1), frame(1, 0x10, 0xf4, 0x51, 1),
            frame(1, 0xf4, 0x63, 0x51, 1), frame(1, 0x63, 0xf4, 0x7f, 0x11, 0x78),
            frame(1, 0x63, 0xf4, 0x51, 1));
        DiagnosticSocket socket = fragmented(stream);

        HeadUnitDiagnostics.restart(socket);

        assertArrayEquals(REQUEST, socket.output.toByteArray());
    }

    @Test public void acknowledgementAloneOrWrongResetTypeDoesNotConfirmReset() throws Exception {
        for (byte[] response : new byte[][]{frame(2, 0xf4, 0x63, 0x11, 1), frame(1, 0x63, 0xf4, 0x51, 3)}) {
            DiagnosticSocket socket = fragmented(response);
            try {
                HeadUnitDiagnostics.restart(socket);
                fail("Expected an unconfirmed reset");
            } catch (IOException expected) {
                assertTrue(expected.getMessage().contains("ответ ГУ не получен"));
                assertArrayEquals(REQUEST, socket.output.toByteArray());
            }
        }
    }

    @Test public void negativeResponseIsReportedWithoutRepeatingReset() throws Exception {
        DiagnosticSocket socket = fragmented(frame(1, 0x63, 0xf4, 0x7f, 0x11, 0x22));
        try {
            HeadUnitDiagnostics.restart(socket);
            fail("Expected a negative response");
        } catch (IOException expected) {
            assertTrue(expected.getMessage().contains("NRC 0x22"));
            assertArrayEquals(REQUEST, socket.output.toByteArray());
        }
    }

    @Test public void unreachableHeadUnitAndInvalidFrameLengthsAreReported() throws Exception {
        for (byte[] response : new byte[][]{frame(0x43), frame(0x45),
                new byte[]{0, 1, 0, 1, 0, 1}, new byte[]{-1, -1, -1, -1, 0, 1}}) {
            DiagnosticSocket socket = fragmented(response);
            try {
                HeadUnitDiagnostics.restart(socket);
                fail("Expected a diagnostic protocol error");
            } catch (IOException expected) {
                assertArrayEquals(REQUEST, socket.output.toByteArray());
            }
        }
    }

    @Test public void timeoutOrDisconnectAfterSendIsUnconfirmedAndNeverRetried() throws Exception {
        for (IOException error : new IOException[]{new SocketTimeoutException(), new SocketException("reset")}) {
            DiagnosticSocket socket = new DiagnosticSocket(new InputStream() {
                @Override public int read() throws IOException { throw error; }
            });
            try {
                HeadUnitDiagnostics.restart(socket);
                fail("Expected an unconfirmed reset");
            } catch (IOException expected) {
                assertSame(error, expected.getCause());
                assertArrayEquals(REQUEST, socket.output.toByteArray());
            }
        }
    }

    private static DiagnosticSocket fragmented(byte[] data) {
        return new DiagnosticSocket(new ByteArrayInputStream(data) {
            @Override public synchronized int read(byte[] buffer, int offset, int count) {
                return super.read(buffer, offset, Math.min(1, count));
            }
        });
    }

    private static byte[] frame(int control, int... payload) {
        ByteBuffer buffer = ByteBuffer.allocate(6 + payload.length);
        buffer.putInt(payload.length).putShort((short) control);
        for (int value : payload) buffer.put((byte) value);
        return buffer.array();
    }

    private static byte[] join(byte[]... frames) throws IOException {
        ByteArrayOutputStream stream = new ByteArrayOutputStream();
        for (byte[] frame : frames) stream.write(frame);
        return stream.toByteArray();
    }
}
