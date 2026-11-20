package ru.nbtaad.prototype;

import java.net.Inet4Address;
import java.net.InetAddress;
import org.junit.Test;
import static org.junit.Assert.*;

public class EnetConnectionTest {
    @Test public void tetheredRouteStaysWithinItsSubnet() throws Exception {
        Inet4Address source = (Inet4Address) InetAddress.getByName("192.168.44.65");
        EnetConnection route = new EnetConnection(null, "eth0", source, 26);

        assertEquals("192.168.44.127", route.broadcastAddress().getHostAddress());
        assertFalse(route.acceptsPeer(source));
        assertFalse(route.acceptsPeer(InetAddress.getByName("192.168.44.64")));
        assertFalse(route.acceptsPeer(InetAddress.getByName("192.168.44.127")));
        assertFalse(route.acceptsPeer(InetAddress.getByName("192.168.44.128")));
        assertTrue(route.acceptsPeer(InetAddress.getByName("192.168.44.90")));
        assertEquals(61, route.scanPeers().size());
    }

    @Test public void wideLinkLocalSubnetDoesNotExpandTcpScan() throws Exception {
        Inet4Address source = (Inet4Address) InetAddress.getByName("169.254.107.10");
        EnetConnection route = new EnetConnection(null, "eth0", source, 16);
        InetAddress gateway = InetAddress.getByName("169.254.14.14");

        assertEquals("169.254.255.255", route.broadcastAddress().getHostAddress());
        assertTrue(route.acceptsPeer(gateway));
        assertFalse(route.scanPeers().contains(gateway));
        assertEquals(255, route.scanPeers().size());
    }

    @Test public void tetheredDiagnosticGatewayAndStandardSshCanBeInDifferentSubnets() throws Exception {
        Inet4Address source = (Inet4Address) InetAddress.getByName("10.42.35.1");
        EnetConnection route = new EnetConnection(null, "eth0", source, 24);

        assertTrue(route.acceptsPeer(InetAddress.getByName("10.42.35.14")));
        assertTrue(route.scanPeers().contains(InetAddress.getByName("10.42.35.119")));
        assertFalse(route.isInSubnet(InetAddress.getByName("169.254.199.99")));
        assertFalse(route.acceptsPeer(InetAddress.getByName("169.254.199.119")));
    }
}
