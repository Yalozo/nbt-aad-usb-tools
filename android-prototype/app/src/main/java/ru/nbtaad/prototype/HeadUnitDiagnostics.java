package ru.nbtaad.prototype;

import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.net.Socket;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

final class HeadUnitDiagnostics {
    private static final byte[] RESET_REQUEST = {0, 0, 0, 4, 0, 1, (byte) 0xf4, 0x63, 0x11, 1};

    static void restart(Socket socket) throws IOException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        socket.getOutputStream().write(RESET_REQUEST);
        socket.getOutputStream().flush();
        InputStream input = socket.getInputStream();
        try {
            while (true) {
                byte[] header = new byte[6];
                readFully(socket, input, header, deadline);
                ByteBuffer fields = ByteBuffer.wrap(header);
                int length = fields.getInt();
                int control = fields.getShort() & 0xffff;
                if (length < 0 || length > 65536) throw new IOException("Недопустимая длина ответа HSFZ.");
                byte[] data = new byte[length];
                readFully(socket, input, data, deadline);
                if (control == 0x43 || control == 0x45) {
                    throw new IOException("Шлюз сообщил о недоступности ГУ для перезагрузки.");
                }
                if (control != 1 || data.length < 4 || (data[0] & 0xff) != 0x63
                    || (data[1] & 0xff) != 0xf4) continue;
                if (data.length == 4 && data[2] == 0x51 && data[3] == 1) return;
                if (data.length == 5 && data[2] == 0x7f && data[3] == 0x11) {
                    int code = data[4] & 0xff;
                    if (code == 0x78) continue;
                    throw new IOException(String.format(Locale.ROOT,
                        "ГУ отклонило ECUReset (NRC 0x%02X).", code));
                }
            }
        } catch (EOFException | SocketTimeoutException | SocketException e) {
            throw new IOException("Запрос ECUReset отправлен, ответ ГУ не получен. Проверьте перезагрузку ГУ.", e);
        }
    }

    private static void readFully(Socket socket, InputStream input, byte[] data, long deadline) throws IOException {
        int offset = 0;
        while (offset < data.length) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) throw new SocketTimeoutException("Истекло время ожидания ECUReset.");
            socket.setSoTimeout((int) Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining)));
            int count = input.read(data, offset, data.length - offset);
            if (count < 0) throw new EOFException("Диагностическое соединение закрыто.");
            offset += count;
        }
    }
}
