package ru.nbtaad.prototype;

import android.net.Network;
import java.io.IOException;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.List;

final class EnetConnection {
    final Network network;
    final String interfaceName;
    final Inet4Address address;
    final int prefixLength;
    final boolean wifi;

    EnetConnection(Network network, String interfaceName, Inet4Address address, int prefixLength) {
        this(network, interfaceName, address, prefixLength, false);
    }

    EnetConnection(Network network, String interfaceName, Inet4Address address, int prefixLength, boolean wifi) {
        if (prefixLength < 0 || prefixLength > 32) throw new IllegalArgumentException("Префикс IPv4");
        this.network = network;
        this.interfaceName = interfaceName;
        this.address = address;
        this.prefixLength = prefixLength;
        this.wifi = wifi;
    }

    boolean usesLocalAddress() {
        return network == null;
    }

    EnetConnection localAddressFallback() {
        if (usesLocalAddress() || prefixLength == 0
            || !(address.isSiteLocalAddress() || address.isLinkLocalAddress())) return this;
        return new EnetConnection(null, interfaceName, address, prefixLength, wifi);
    }

    boolean acceptsPeer(InetAddress peer) {
        if (!(peer instanceof Inet4Address) || peer.equals(address)) return false;
        return !usesLocalAddress() || isInSubnet(peer);
    }

    boolean isInSubnet(InetAddress peer) {
        if (!(peer instanceof Inet4Address)) return false;
        int mask = mask(prefixLength);
        int base = bits(address) & mask;
        int target = bits(peer);
        return (target & mask) == base
            && (prefixLength >= 31 || (target != base && target != (base | ~mask)));
    }

    InetAddress broadcastAddress() {
        return prefixLength < 31 ? ipv4(bits(address) | ~mask(prefixLength)) : null;
    }

    List<InetAddress> scanPeers() {
        int scanPrefix = Math.max(24, prefixLength);
        int base = bits(address) & mask(scanPrefix);
        int size = 1 << (32 - scanPrefix);
        List<InetAddress> peers = new ArrayList<>();
        for (int i = 0; i < size; i++) {
            InetAddress peer = ipv4(base + i);
            if (acceptsPeer(peer)) peers.add(peer);
        }
        return peers;
    }

    static List<EnetConnection> routesForPeer(List<EnetConnection> routes, InetAddress peer) {
        List<EnetConnection> matches = new ArrayList<>();
        for (EnetConnection route : routes) {
            if (route.isInSubnet(peer) && route.acceptsPeer(peer)) matches.add(route);
        }
        return matches;
    }

    Socket connect(InetAddress peer, int port, int timeout) throws IOException {
        if (!acceptsPeer(peer)) throw new IOException("Адрес вне выбранной сети: " + peer.getHostAddress());
        Socket socket = new Socket();
        try {
            if (usesLocalAddress()) socket.bind(new InetSocketAddress(address, 0));
            else network.bindSocket(socket);
            socket.connect(new InetSocketAddress(peer, port), timeout);
            return socket;
        } catch (IOException | RuntimeException e) {
            try { socket.close(); } catch (IOException ignored) {}
            throw e;
        }
    }

    DatagramSocket openDiscoverySocket(boolean bindSourceAddress) throws IOException {
        DatagramSocket socket = new DatagramSocket(null);
        try {
            socket.setBroadcast(true);
            if (usesLocalAddress()) socket.bind(new InetSocketAddress(address, 7811));
            else {
                network.bindSocket(socket);
                socket.bind(bindSourceAddress ? new InetSocketAddress(address, 0) : new InetSocketAddress(0));
            }
            return socket;
        } catch (IOException | RuntimeException e) {
            socket.close();
            throw e;
        }
    }

    private static int mask(int prefix) {
        return prefix == 0 ? 0 : -1 << (32 - prefix);
    }

    private static int bits(InetAddress ip) {
        int result = 0;
        for (byte b : ip.getAddress()) result = (result << 8) | (b & 255);
        return result;
    }

    private static InetAddress ipv4(int bits) {
        try {
            return InetAddress.getByAddress(new byte[]{(byte) (bits >>> 24), (byte) (bits >>> 16),
                (byte) (bits >>> 8), (byte) bits});
        } catch (UnknownHostException impossible) {
            throw new AssertionError(impossible);
        }
    }
}
