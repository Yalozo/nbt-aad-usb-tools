package ru.nbtaad.prototype;

import java.io.IOException;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.util.Arrays;
import java.util.Collections;
import org.junit.Test;
import static org.junit.Assert.*;

public class HeadUnitNetworkTest {
    @Test public void discoveredWifiGatewayRemainsSelectedWhenSshIsClosed() throws Exception {
        EnetConnection wifi = new EnetConnection(null, "wlan0",
            (Inet4Address) InetAddress.getByName("192.168.1.133"), 24, true);
        MainActivity.GatewaySearch found = new MainActivity.GatewaySearch(wifi,
            Collections.singleton("192.168.1.131"));

        MainActivity.GatewaySearch selected = MainActivity.GatewaySearch.select(Collections.singletonList(found));

        assertSame(wifi, selected.route);
        assertEquals(Collections.singleton("192.168.1.131"), selected.addresses);
        assertSame(wifi, EnetConnection.routesForPeer(Collections.singletonList(wifi),
            InetAddress.getByName("192.168.1.131")).get(0));
        assertTrue(wifi.scanPeers().contains(InetAddress.getByName("192.168.1.119")));
        assertFalse(wifi.scanPeers().contains(InetAddress.getByName("192.168.2.119")));
    }

    @Test public void multipleGatewaysRequireAnExplicitChoice() throws Exception {
        EnetConnection wifi = new EnetConnection(null, "wlan0",
            (Inet4Address) InetAddress.getByName("192.168.1.133"), 24, true);
        MainActivity.GatewaySearch first = new MainActivity.GatewaySearch(wifi,
            Collections.singleton("192.168.1.131"));
        MainActivity.GatewaySearch second = new MainActivity.GatewaySearch(wifi,
            Collections.singleton("192.168.1.132"));
        try {
            MainActivity.GatewaySearch.select(Arrays.asList(first, second));
            fail("Expected an explicit gateway choice");
        } catch (IOException expected) {
            assertTrue(expected.getMessage().contains("несколько шлюзов"));
        }
    }
}
