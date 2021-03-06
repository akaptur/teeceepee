"""
Unit tests for TCPSocket.

Mocks a listener instead of sending real packets.

"""

from tcp import TCPSocket
from scapy.all import IP, TCP, Ether, rdpcap
from mock_listener import MockListener


def test_syn():
    listener = MockListener()
    conn = TCPSocket(listener)
    conn.connect("localhost", 80)

    assert conn.state == "SYN-SENT"
    pkts = listener.received_packets
    assert len(pkts) == 1
    assert pkts[0].sprintf("%TCP.flags%") == "S"


def test_handshake_client():
    listener = MockListener()
    conn = TCPSocket(listener)
    conn.connect("localhost", 80)
    initial_seq = conn.seq

    tcp_packet = TCP(dport=conn.src_port, flags="SA", seq=100, ack=initial_seq + 1)
    syn_ack = Ether() / IP(dst=conn.src_ip) / tcp_packet
    listener.dispatch(syn_ack)

    assert conn.seq == initial_seq + 1
    assert conn.state == "ESTABLISHED"

    # We should have sent exactly two packets
    # Check that they look okay
    pkts = listener.received_packets
    assert len(pkts) == 2
    syn, ack = pkts
    assert ack.seq == syn.seq + 1
    assert syn.sprintf("%TCP.flags%") == "S"
    assert ack.sprintf("%TCP.flags%") == "A"


def create_session(packet_log):
    listener = MockListener()
    syn = packet_log[0]
    listener.source_port = syn.sport - 1
    conn = TCPSocket(listener)
    conn.connect(syn.payload.dst, syn.dport)
    # Change the sequence number so that we can test it
    conn.seq = syn.seq
    return listener, conn

def check_mostly_same(pkt1, pkt2):
    assert pkt1.seq == pkt2.seq
    assert pkt1.ack == pkt2.ack
    assert pkt1.sprintf("%TCP.flags%") == pkt2.sprintf("%TCP.flags%")
    if not hasattr(pkt1, 'load'):
        pkt1.load = None
    if not hasattr(pkt2, 'load'):
        pkt2.load = None
    assert pkt1.load == pkt2.load

def test_send_push_ack():
    packet_log = rdpcap("test/inputs/localhost-wget.pcap")
    listener, conn = create_session(packet_log)

    _, syn_ack, _, push_ack = packet_log[:4]
    listener.dispatch(syn_ack)
    assert conn.state == "ESTABLISHED"

    # Extract the payload (3 levels down: Ether, IP, TCP)
    payload = str(push_ack.payload.payload.payload)
    conn.send(payload)

    # Check to make sure the PUSH-ACK packet packet that gets sent looks good
    our_push_ack = listener.received_packets[-1]
    check_mostly_same(our_push_ack, push_ack)


def test_fin_ack():
    packet_log = rdpcap("test/inputs/tiniest-session.pcap")
    listener, conn = create_session(packet_log)

    _, syn_ack_log, _, our_fin_ack_log, their_fin_ack_log, our_ack_log = packet_log
    listener.dispatch(syn_ack_log)

    conn.close()
    listener.dispatch(their_fin_ack_log)

    assert len(listener.received_packets) == 4

    our_ack = listener.received_packets[-1]
    check_mostly_same(our_ack, our_ack_log)

    assert conn.state == "TIME-WAIT"


def check_replay(listener, conn, packet_log, check=True):
    """
    Check if replaying the packets gives the same result
    """
    ip, port = conn.src_ip, conn.src_port
    is_from_source = lambda x: x.payload.dst == ip and x.dport == port

    incoming = [x for x in packet_log if is_from_source(x)]
    outgoing = [x for x in packet_log if not is_from_source(x)]

    for pkt in incoming:
        listener.dispatch(pkt)

    if not check:
        return

    our_outgoing = listener.received_packets[-len(outgoing):]

    for ours, actual in zip(our_outgoing, outgoing):
        check_mostly_same(ours, actual)


def test_recv_one_packet():
    packet_log = rdpcap("test/inputs/localhost-wget.pcap")
    listener, conn = create_session(packet_log)

    _, syn_ack, _, push_ack = packet_log[:4]

    listener.dispatch(syn_ack)
    payload = str(push_ack.payload.payload.payload)
    conn.send(payload)

    # Check that the PUSH/ACK sequence is the same
    check_replay(listener, conn, packet_log[4:7])

    # Check that recv() actually works
    assert conn.recv() == str(packet_log[5].payload.payload.payload)

def test_recv_many_packets_in_order():
    packet_log = rdpcap("test/inputs/wget-36000-nums.pcap")
    listener, conn = create_session(packet_log)
    _, syn_ack, _, push_ack = packet_log[:4]

    listener.dispatch(syn_ack)
    payload = str(push_ack.payload.payload.payload)
    conn.send(payload)

    check_replay(listener, conn, packet_log[4:], check=False)

    # Check that the contents of the packet is right
    recv = conn.recv()
    assert recv[-36001:-1]  == "1234567890" * 3600

def test_recv_many_packets_out_of_order():
    packet_log = rdpcap("test/inputs/wget-36000-nums.pcap")
    listener, conn = create_session(packet_log)
    _, syn_ack, _, push_ack = packet_log[:4]

    listener.dispatch(syn_ack)
    payload = str(push_ack.payload.payload.payload)
    conn.send(payload)

    p1, p2, p3  = packet_log[5], packet_log[7], packet_log[8]

    # Send the packets out of order and repeated
    listener.dispatch(p2)
    listener.dispatch(p3)
    listener.dispatch(p2)
    listener.dispatch(p1) # Right
    listener.dispatch(p3)
    listener.dispatch(p2) # Right
    listener.dispatch(p3) # Right

    # Check that the contents of the packet is right
    recv = conn.recv()
    assert recv[-36001:-1]  == "1234567890" * 3600

def test_bind_handshake():
    packet_log = rdpcap("test/inputs/tiniest-session.pcap")
    syn, syn_ack, ack, client_fin_ack, server_fin_ack, client_ack = packet_log

    listener = MockListener()
    conn = TCPSocket(listener)
    conn.seq = syn_ack.seq
    conn.bind(syn.payload.dst, syn.dport)

    listener.dispatch(syn)
    listener.dispatch(ack)

    assert len(listener.received_packets) == 1
    assert conn.state == "ESTABLISHED"
    check_mostly_same(listener.received_packets[0], syn_ack)

def test_tiny_session_server():
    """
    Test the whole tiny session, from a server POV.
    """
    packet_log = rdpcap("test/inputs/tiniest-session.pcap")
    syn, syn_ack = packet_log[:2]

    listener = MockListener()
    conn = TCPSocket(listener)
    conn.seq = syn_ack.seq

    conn.bind(syn.payload.dst, syn.dport)


    check_replay(listener, conn, packet_log)

def test_reset():
    listener = MockListener()

    packet_log = rdpcap("test/inputs/tiniest-session.pcap")
    listener, conn = create_session(packet_log)
    syn_ack = packet_log[1]

    listener.dispatch(syn_ack)

    reset = syn_ack.copy()
    reset.payload.payload.flags = "R"
    reset.seq += 1
    listener.dispatch(reset)

    assert conn.state == "CLOSED"

