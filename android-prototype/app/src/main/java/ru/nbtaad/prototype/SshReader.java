package ru.nbtaad.prototype;

import android.content.Context;
import com.jcraft.jsch.*;
import java.io.*;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

final class SshReader implements AutoCloseable {
    interface Confirm { boolean accept(String host, String fingerprint) throws Exception; }
    private final Session session;
    private final String node;

    SshReader(Context context, EnetConnection route, String host, int port, String password, Confirm confirm) throws Exception {
        JSch jsch=new JSch();
        final android.content.SharedPreferences prefs=context.getSharedPreferences("hostkeys",0);
        jsch.setHostKeyRepository(new HostKeyRepository() {
            public int check(String h, byte[] key) {
                String old=prefs.getString(h,null), current=android.util.Base64.encodeToString(key,android.util.Base64.NO_WRAP);
                return old==null?NOT_INCLUDED:(old.equals(current)?OK:CHANGED);
            }
            public void add(HostKey key, UserInfo ui) { prefs.edit().putString(key.getHost(),key.getKey()).commit(); }
            public void remove(String h,String t) {} public void remove(String h,String t,byte[] k) {}
            public String getKnownHostsRepositoryID(){ return "app-private"; }
            public HostKey[] getHostKey(){ return new HostKey[0]; }
            public HostKey[] getHostKey(String h,String t){ return new HostKey[0]; }
        });
        session=jsch.getSession("root",host,port);
        session.setSocketFactory(new SocketFactory(){
            public Socket createSocket(String h, int p) throws IOException {
                return route.connect(InetAddress.getByName(h), p, 10000);
            }
            public InputStream getInputStream(Socket s) throws IOException{return s.getInputStream();}
            public OutputStream getOutputStream(Socket s) throws IOException{return s.getOutputStream();}
        });
        session.setPassword(password); session.setConfig("StrictHostKeyChecking","ask");
        session.setConfig("server_host_key", session.getConfig("server_host_key") + ",ssh-rsa");
        session.setUserInfo(new UserInfo(){
            public String getPassphrase(){return null;} public String getPassword(){return null;}
            public boolean promptPassword(String m){return false;} public boolean promptPassphrase(String m){return false;}
            public void showMessage(String m){}
            public boolean promptYesNo(String m) {
                if (!m.contains("fingerprint") || m.contains("HAS CHANGED")) return false;
                try { return confirm.accept(host,m); } catch(Exception e){return false;}
            }
        });
        try {
            session.setServerAliveInterval(10000);
            session.setServerAliveCountMax(3);
            session.connect(12000);
            node = detect();
        } catch (Exception e) {
            session.disconnect();
            throw e;
        }
    }

    private String detect() throws Exception {
        String[] lines = text(runRaw(session, "uname -s; uname -n", null, 15000, 2048)).trim().split("\\r?\\n");
        if (lines.length != 2 || !lines[0].equals("QNX")) throw new IOException("Ожидалось ГУ QNX.");
        String detected = lines[1].split("\\.")[0];
        if (!detected.equals("hu-omap") && !detected.equals("hu-jacinto")) throw new IOException("Неизвестный узел ГУ.");
        String layout = text(runRaw(session, onOmap(detected, "if test -d /fs/sda0/opt/hmi/ID5; "
            + "then echo ID5; else echo ID5_MISSING; fi"), null, 15000, 2048)).trim();
        if (layout.equals("ID5_MISSING")) throw new UnsupportedHeadUnitException();
        if (!layout.equals("ID5")) {
            throw new IOException("ГУ вернуло неожиданный ответ при проверке интерфейса ID5/ID6.");
        }
        String credentials = text(runRaw(session, onOmap(detected,
            "echo NBT_AAD_SHELL_PID=$$; pidin -p $$ -F \"%a %W\""), null, 15000, 2048));
        String[] rows = credentials.trim().split("\\r?\\n");
        if (!rows[0].matches("NBT_AAD_SHELL_PID=[0-9]+")) throw new IOException("Не удалось проверить root-доступ.");
        String pid = rows[0].substring("NBT_AAD_SHELL_PID=".length());
        boolean root = false;
        for (int i = 1; i < rows.length; i++) {
            String[] fields = rows[i].trim().split("\\s+");
            if (!fields[0].equals(pid)) continue;
            if (fields.length != 2 || !fields[1].equals("0")) throw new IOException("Root-доступ не подтверждён.");
            root = true;
        }
        if (!root) throw new IOException("Root-доступ не подтверждён.");
        return detected;
    }

    String hostKey() { return session.getHostKey().getKey(); }

    String firmware() throws Exception {
        String version = text(run("cat /opt/sys/etc/nbt_version.txt", null, 5000, 256)).trim();
        if (!version.matches("(?i)NBTevo_[a-z0-9][a-z0-9_]{0,63}")) {
            throw new IOException("Не удалось распознать версию прошивки.");
        }
        return node + ": " + version;
    }

    static String quote(String text) {
        return "'" + text.replace("'", "'\"'\"'") + "'";
    }

    private static String onOmap(String node, String command) {
        return node.equals("hu-jacinto") ? "on -f /net/hu-omap /bin/sh -c " + quote(command) : command;
    }

    byte[] run(String command, byte[] input, long timeout, int limit) throws Exception {
        return runRaw(session, onOmap(node, command), input, timeout, limit);
    }

    static byte[] runRaw(Session session, String command, byte[] input, long timeout, int limit) throws Exception {
        ChannelExec channel = (ChannelExec) session.openChannel("exec");
        channel.setCommand(command);
        channel.setInputStream(input == null ? null : new ByteArrayInputStream(input));
        CommandOutput captured = new CommandOutput(limit);
        channel.setOutputStream(captured.stream(captured.stdout));
        channel.setErrStream(captured.stream(captured.stderr));
        try {
            channel.connect(12000);
            long deadline = System.nanoTime() + java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(timeout);
            while (!channel.isClosed()) {
                captured.checkLimit();
                if (System.nanoTime() - deadline >= 0) {
                    throw captured.failure("Команда ГУ не завершилась за отведённое время.", -2);
                }
                Thread.sleep(20);
            }
            captured.checkLimit();
            int status = channel.getExitStatus();
            if (status != 0) {
                String message = status < 0 ? "SSH закрылся без кода завершения." : "Команда ГУ завершилась с кодом " + status + ".";
                throw captured.failure(message, status);
            }
            return captured.bytes();
        } finally {
            channel.disconnect();
        }
    }

    private static final class CommandOutput {
        final ByteArrayOutputStream stdout = new ByteArrayOutputStream();
        final ByteArrayOutputStream stderr = new ByteArrayOutputStream();
        final int limit;
        private boolean overflow;

        CommandOutput(int limit) { this.limit = limit; }

        OutputStream stream(ByteArrayOutputStream target) {
            return new OutputStream() {
                @Override public void write(int b) throws IOException {
                    append(target, new byte[]{(byte) b}, 0, 1);
                }

                @Override public void write(byte[] data, int offset, int length) throws IOException {
                    append(target, data, offset, length);
                }
            };
        }

        private synchronized void append(ByteArrayOutputStream target, byte[] data, int offset, int length)
                throws IOException {
            if (length > limit - stdout.size() - stderr.size()) overflow = true;
            checkLimit();
            target.write(data, offset, length);
        }

        synchronized void checkLimit() throws IOException {
            if (overflow) throw new IOException("Ответ ГУ превышает ограничение.");
        }

        synchronized byte[] bytes() { return stdout.toByteArray(); }

        synchronized CommandFailure failure(String message, int status) {
            return new CommandFailure(message, status, stdout, stderr);
        }
    }

    static String text(byte[] bytes) { return new String(bytes, StandardCharsets.UTF_8); }

    static final class UnsupportedHeadUnitException extends IOException {
        UnsupportedHeadUnitException() {
            super("На ГУ отсутствует каталог интерфейса ID5 (/fs/sda0/opt/hmi/ID5). "
                + "Для установки NBT-AAD требуется NBT Evo с прошивкой ID5 или ID6. "
                + "Штатная прошивка ID4 не поддерживается.");
        }
    }

    static final class CommandFailure extends IOException {
        final int code;
        final String output;

        CommandFailure(String message, int code, ByteArrayOutputStream output, ByteArrayOutputStream errors) {
            super(message + tail(text(errors.toByteArray()), 1500));
            this.code = code;
            this.output = text(output.toByteArray());
        }

        private static String tail(String text, int limit) {
            return text.isEmpty() ? "" : " " + text.substring(Math.max(0, text.length() - limit)).trim();
        }
    }

    @Override public void close() { session.disconnect(); }
}
