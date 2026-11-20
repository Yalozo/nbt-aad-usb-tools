package ru.nbtaad.prototype;

import android.net.Network;
import java.io.*;
import java.net.*;
import java.util.concurrent.*;
import okhttp3.*;
import okio.ByteString;

final class UnlockRelay {
    private static final Object CLOSED=new Object();
    static void activate(EnetConnection route, Network internet, String gateway) throws Exception {
        BlockingQueue<Object> events=new LinkedBlockingQueue<>(256);
        OkHttpClient client=new OkHttpClient.Builder().socketFactory(internet.getSocketFactory())
            .dns(host -> java.util.Arrays.asList(internet.getAllByName(host)))
            .connectTimeout(10,TimeUnit.SECONDS).readTimeout(0,TimeUnit.SECONDS).build();
        WebSocket ws=client.newWebSocket(new Request.Builder().url("wss://fix-bmw.ru:8443").header("User-Agent","NBT-AAD-PC/0.1").build(),new WebSocketListener(){
            private void offer(Object x) { if(!events.offer(x)) { events.clear(); events.offer(new IOException("Переполнение очереди relay")); } }
            @Override public void onOpen(WebSocket w, Response r){offer(Boolean.TRUE);}
            @Override public void onMessage(WebSocket w,ByteString b){offer(b.toByteArray());}
            @Override public void onMessage(WebSocket w,String s){offer(new IOException("Ожидался бинарный ответ relay"));}
            @Override public void onFailure(WebSocket w,Throwable t,Response r){offer(new IOException("Ошибка соединения с сервисом",t));}
            @Override public void onClosed(WebSocket w,int code,String reason){offer(CLOSED);}
            @Override public void onClosing(WebSocket w,int code,String reason){offer(CLOSED);}
        });
        Socket car=null;
        try {
            Object opened=events.poll(12,TimeUnit.SECONDS);
            if(opened!=Boolean.TRUE) throw new IOException("Не удалось подключиться к сервису открытия SSH");
            car = route.connect(InetAddress.getByName(gateway), 6801, 6000);
            final Socket connection=car;
            send(ws,RelayProtocol.field(6,"0.1.0".getBytes("UTF-8")));
            send(ws,new byte[]{1,0,0,0,0,0,0,0,0,0,0,0,0});
            send(ws,RelayProtocol.field(2,"ssh_enable".getBytes("UTF-8")));
            Thread reader=new Thread(() -> {
                try { byte[] b=new byte[32768]; int n; InputStream in=connection.getInputStream();
                    while((n=in.read(b))!=-1) send(ws,RelayProtocol.field(3,java.util.Arrays.copyOf(b,n)));
                } catch(Exception e){ events.offer(new IOException("Ошибка передачи данных автомобиля",e)); }
            }); reader.setDaemon(true); reader.start();
            final boolean[] result={false}, done={false}; RelayProtocol parser=new RelayProtocol();
            RelayProtocol.Sink sink=new RelayProtocol.Sink(){
                public void car(byte[] frame) throws IOException {connection.getOutputStream().write(frame); connection.getOutputStream().flush();}
                public void done(boolean ok){done[0]=true; result[0]=ok;}
            };
            long deadline=android.os.SystemClock.elapsedRealtime()+75000;
            while(!done[0]) {
                long remaining=deadline-android.os.SystemClock.elapsedRealtime(); if(remaining<=0)throw new IOException("Время ожидания открытия SSH истекло");
                Object event=events.poll(remaining,TimeUnit.MILLISECONDS);
                if(event instanceof byte[]) parser.accept((byte[])event,sink);
                else if(event instanceof Exception) throw (Exception)event;
                else if(event==CLOSED || event==null) throw new IOException("Сервис закрыл соединение без подтверждения");
                else throw new IOException("Неожиданный ответ сервиса");
            }
            if(!result[0])throw new IOException("Сервис отказал в открытии SSH");
        } finally { if(car!=null)try{car.close();}catch(IOException ignored){} ws.cancel(); client.dispatcher().executorService().shutdown(); client.connectionPool().evictAll(); }
    }
    private static void send(WebSocket ws,byte[] data) throws IOException { if(!ws.send(ByteString.of(data)))throw new IOException("Ошибка отправки в relay"); }
}
