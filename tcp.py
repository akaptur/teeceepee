from scapy.all import TCP, IP, send
import random
from Queue import Queue
import time

class BadPacketError(Exception):
    pass

class TCPSocket(object):
    def __init__(self, listener, src_ip='127.0.0.1', verbose=0):
        self.state = "CLOSED"
        self.verbose = verbose
        self.src_ip = src_ip
        self.recv_buffer = ""
        self.listener = listener
        self.seq = self._generate_seq()
        self.last_ack_sent = None


    @staticmethod
    def _has_load(packet):
        return hasattr(packet, 'load') and packet.load is not None

    def _set_dest(self, host, port):
        self.dest_port = port
        self.dest_ip = host
        self.ip_header = IP(dst=self.dest_ip, src=self.src_ip)

    def connect(self, host, port):
        self._set_dest(host, port)
        self.src_port = self.listener.get_port()
        self.listener.open(self.src_ip, self.src_port, self)
        self._send_syn()

    def bind(self, host, port):
        # Right now, listen for only one connection.
        self.src_ip = host
        self.src_port = port
        self.listener.open(self.src_ip, self.src_port, self)
        self.state = "LISTEN"

    @staticmethod
    def _generate_seq():
        return random.randint(0, 100000)

    def _send_syn(self):
        self._send(flags="S")
        self.state = "SYN-SENT"

    def _send(self, **kwargs):
        """Every packet we send should go through here."""
        load = kwargs.pop('load', None)
        flags = kwargs.pop('flags', "")
        packet = TCP(dport=self.dest_port,
                     sport=self.src_port,
                     seq=self.seq,
                     ack=self.last_ack_sent,
                     **kwargs)
        # Always ACK unless it's the first packet
        if self.state == "CLOSED":
            packet.flags = flags
        else:
            packet.flags = flags + "A"
        # Add the IP header
        full_packet = self.ip_header / packet
        # Add the payload
        full_packet.load = load
        # Send the packet over the wire
        self.listener.send(full_packet)
        # Update the sequence number with the number of bytes sent
        if load is not None:
            self.seq += len(load)

    def _send_ack(self, **kwargs):
        """We actually don't need to do much here!"""
        self._send(**kwargs)

    def close(self):
        self.state = "FIN-WAIT-1"
        self._send(flags="F")

    @staticmethod
    def next_seq(packet):
        # really not right.
        tcp_flags = packet.sprintf("%TCP.flags%")
        if TCPSocket._has_load(packet):
            return packet.seq + len(packet.load)
        elif 'S' in tcp_flags or 'F' in tcp_flags:
            return packet.seq + 1
        else:
            return packet.seq

    def handle(self, packet):
        if self.state == "CLOSED":
            return

        if self.last_ack_sent is not None and self.last_ack_sent != packet.seq:
            # We're not in a place to receive this packet. Drop it.
            return

        self.last_ack_sent = max(self.next_seq(packet), self.last_ack_sent)

        if self._has_load(packet):
            self.recv_buffer += packet.load

        recv_flags = packet.sprintf("%TCP.flags%")
        send_flags = ""


        # Handle all the cases for self.state explicitly
        print repr(packet.payload.payload)
        if "R" in recv_flags:
            self.state = "CLOSED"
            return
        elif self.state == "LISTEN" and 'S' in recv_flags:
            send_flags = "S"
            self.state = "SYN-RECEIVED"
            self._set_dest(packet.payload.src, packet.sport)
        elif self.state == "SYN-RECEIVED" and 'A' in recv_flags:
            self.state = "ESTABLISHED"
        elif self.state == "ESTABLISHED" and 'F' in recv_flags:
            self.seq += 1
            send_flags = "F"
            self.state = "LAST-ACK"
        elif self.state == "LAST-ACK" and 'A' in recv_flags:
            self.state = "CLOSED"
        elif self.state == "ESTABLISHED":
            pass
        elif self.state == "SYN-SENT":
            self.seq += 1
            self.state = "ESTABLISHED"
        elif self.state == "FIN-WAIT-1" and 'F' in recv_flags:
            self.seq += 1
            self.state = "TIME-WAIT"
        else:
            raise BadPacketError("Oh no!")

        if recv_flags == "A" and not self._has_load(packet):
            # This is just an ACK. We don't need to ACK the ACK
            return

        self._send_ack(flags=send_flags)


    def send(self, payload):
        # Block
        while self.state != "ESTABLISHED":
            time.sleep(0.001)
        # Do the actual send
        self._send(load=payload, flags="P")


    def recv(self):
        recv = self.recv_buffer
        self.recv_buffer = ""
        return recv

