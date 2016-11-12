import socket
import string
import struct

if __name__ == '__main__':
    sock = socket.socket(socket.AF_UNIX)
    sock.connect('\0marklin-simulator')

    while True:
        cmd = raw_input('> ')
        args = cmd.split()

        name = args[0]
        if name == 'tr':
            assert len(args) == 3
            number = int(args[1])
            speed = int(args[2])

            cmd = struct.pack('BB', speed, number)
            sock.send(cmd)
        elif name == 'sw':
            assert len(args) == 3
            number = int(args[1])
            direction = 0x21 if args[2] == 'S' else 0x22

            cmd = struct.pack('BB', direction, number)
            sock.send(cmd)
        elif name == 'dump':
            assert len(args) == 1
            sock.send('\x85')
            result = sock.recv(10, socket.MSG_WAITALL)
            # This could happen, but nobody cares.
            assert len(result) == 10

            print 'Sensors:'
            print ":".join("{:02x}".format(ord(c)) for c in result)
            for i, c in enumerate(string.uppercase[:5]):
                for j in xrange(2):
                    byte = ord(result[i * 2 + j])
                    for bit in xrange(8, 0, -1):
                        print '%s: %d' % (c + str(j * 8 + bit), byte & 1)
                        byte >>= 1
