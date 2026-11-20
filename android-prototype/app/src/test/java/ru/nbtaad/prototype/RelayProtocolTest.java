package ru.nbtaad.prototype;
import org.junit.Test;
import static org.junit.Assert.*;
import java.io.*;
import java.util.*;
public class RelayProtocolTest {
    static class Sink implements RelayProtocol.Sink { List<byte[]> frames=new ArrayList<>(); Boolean result; public void car(byte[] x){frames.add(x);} public void done(boolean b){result=b;} }
    @Test public void fragmentedFramesAndCompletion() throws Exception {
        RelayProtocol p=new RelayProtocol(); Sink s=new Sink();
        byte[] frame={0,0,0,4,0,1,(byte)0xf4,0x63,0x22,0x01};
        byte[] packet=RelayProtocol.field(0x81,frame);
        for(byte b:packet)p.accept(new byte[]{b},s);
        p.accept(new byte[]{(byte)0x83},s);p.accept(new byte[]{1},s);
        assertArrayEquals(frame,s.frames.get(0));assertEquals(Boolean.TRUE,s.result);
    }
    @Test public void blockedSequenceAcrossFrames() throws Exception {
        RelayProtocol p=new RelayProtocol();Sink s=new Sink();
        p.accept(RelayProtocol.field(0x81,new byte[]{0,0,0,4,0,1,1,2,0x31,1}),s);
        try {p.accept(RelayProtocol.field(0x81,new byte[]{0,0,0,5,0,1,1,2,(byte)0xfd,(byte)0xee,0x38}),s);fail();}catch(IOException expected){}
    }
    @Test public void rejectCompletionWithPartialHsfz() throws Exception {
        RelayProtocol p=new RelayProtocol();Sink s=new Sink();p.accept(RelayProtocol.field(0x81,new byte[]{0,0,0}),s);
        try {p.accept(new byte[]{(byte)0x83,1},s);fail();}catch(IOException expected){}
    }
    @Test public void rejectOversizedAndUnknown() throws Exception {
        try {new RelayProtocol().accept(new byte[]{(byte)0x81,0,2,0,1},new Sink());fail();}catch(IOException expected){}
        try {new RelayProtocol().accept(new byte[]{3},new Sink());fail();}catch(IOException expected){}
    }

    @Test public void serviceLogFramesAreConsumedWithoutParsingTheirBodyAsCommands() throws Exception {
        RelayProtocol parser = new RelayProtocol();
        Sink sink = new Sink();
        byte[] log = {(byte) 0x82, 4, 0, 0, 0, 3, (byte) 0x84, (byte) 0x81, (byte) 0x83};
        for (byte value : log) parser.accept(new byte[]{value}, sink);
        parser.accept(new byte[]{(byte) 0x83, 1}, sink);
        assertEquals(Boolean.TRUE, sink.result);
        assertTrue(sink.frames.isEmpty());
    }

    @Test public void unknownCommandHasItsOwnErrorAndReportsTheOpcode() throws Exception {
        try {
            new RelayProtocol().accept(new byte[]{(byte) 0x84}, new Sink());
            fail("Expected an unknown-command error");
        } catch (RelayProtocol.UnknownCommandException expected) {
            assertTrue(expected.getMessage().contains("0x84"));
        }
    }
}
