package ru.nbtaad.prototype;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Arrays;

/** Incremental parsing: websocket boundaries are unrelated to relay or HSFZ frames. */
final class RelayProtocol {
    static final class UnknownCommandException extends IOException {
        UnknownCommandException(int opcode) {
            super(String.format(java.util.Locale.ROOT, "Неизвестная команда relay: 0x%02X", opcode));
        }
    }
    interface Sink { void car(byte[] frame) throws IOException; void done(boolean success) throws IOException; }
    private byte[] pending = new byte[0], hsfz = new byte[0], tail = new byte[0];
    private boolean completed;
    static byte[] field(int op, byte[] data) throws IOException {
        if (data.length > 131072) throw new IOException("Relay: слишком большой пакет");
        byte[] result = new byte[data.length + 5]; result[0] = (byte)op;
        int n = data.length; result[1]=(byte)(n>>>24); result[2]=(byte)(n>>>16); result[3]=(byte)(n>>>8); result[4]=(byte)n;
        System.arraycopy(data,0,result,5,n); return result;
    }
    private static byte[] append(byte[] a, byte[] b) throws IOException {
        if (a.length + b.length > 262144) throw new IOException("Переполнение буфера");
        byte[] r = Arrays.copyOf(a,a.length+b.length); System.arraycopy(b,0,r,a.length,b.length); return r;
    }
    private static int u32(byte[] a,int p) throws IOException {
        long v=((long)(a[p]&255)<<24)|((long)(a[p+1]&255)<<16)|((long)(a[p+2]&255)<<8)|(a[p+3]&255);
        if(v>Integer.MAX_VALUE) throw new IOException("Недопустимая длина"); return (int)v;
    }
    void accept(byte[] data, Sink sink) throws IOException {
        if (completed && data.length > 0) throw new IOException("Данные после завершения relay");
        pending=append(pending,data);
        while(pending.length>0) {
            int op=pending[0]&255;
            if(op==0x83) {
                if(pending.length<2) return;
                int status=pending[1]&255;
                if(status>1 || pending.length!=2 || hsfz.length!=0) throw new IOException("Некорректное завершение relay");
                completed=true; pending=new byte[0]; sink.done(status==1); return;
            }
            if(op!=0x81 && op!=0x82) throw new UnknownCommandException(op);
            int header=op==0x82?6:5; if(pending.length<header) return;
            int len=u32(pending,header-4); if(len>131072) throw new IOException("Пакет relay слишком велик");
            if(pending.length<header+len) return;
            byte[] body=Arrays.copyOfRange(pending,header,header+len);
            pending=Arrays.copyOfRange(pending,header+len,pending.length);
            if(op==0x81) acceptHsfz(body,sink);
        }
    }
    private void acceptHsfz(byte[] bytes,Sink sink) throws IOException {
        hsfz=append(hsfz,bytes);
        while(hsfz.length>=6) {
            int len=u32(hsfz,0), control=((hsfz[4]&255)<<8)|(hsfz[5]&255);
            if(len<2 || len>65536 || (control!=1 && control!=2)) throw new IOException("Некорректный HSFZ кадр");
            if(hsfz.length<len+6) return;
            byte[] frame=Arrays.copyOfRange(hsfz,0,len+6);
            byte[] payload=Arrays.copyOfRange(frame,8,frame.length), joined=append(tail,payload);
            byte[] blocked={0x31,0x01,(byte)0xfd,(byte)0xee,0x38};
            for(int i=0;i+blocked.length<=joined.length;i++) {
                boolean match=true; for(int j=0;j<blocked.length;j++) if(joined[i+j]!=blocked[j]) {match=false;break;}
                if(match) throw new IOException("Заблокирована диагностическая команда 31 01 FD EE 38");
            }
            tail=Arrays.copyOfRange(joined,Math.max(0,joined.length-4),joined.length);
            hsfz=Arrays.copyOfRange(hsfz,len+6,hsfz.length); sink.car(frame);
        }
    }
}
