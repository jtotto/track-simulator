import socket
import string
import struct
import thread

if __name__ == '__main__':
    marklin_sock = socket.socket(socket.AF_UNIX)
    marklin_sock.connect('\0marklin-simulator')

    timer_sock = socket.socket(socket.AF_UNIX)
    timer_sock.connect('\0timer-simulator')

    class State:
        def __init__(self):
            self.time = 1

    state = State()

    def timer():
        while True:
            c = timer_sock.recv(1)
            assert c == '0'

            state.time += 1

    thread.start_new_thread(timer, ())

    while True:
        cmd = raw_input('> ')
        args = cmd.split()

        name = args[0]
        if name == 'tr':
            assert len(args) == 3
            number = int(args[1])
            speed = int(args[2])

            cmd = struct.pack('BB', speed, number)
            marklin_sock.send(cmd)
        elif name == 'sw':
            assert len(args) == 3
            number = int(args[1])
            direction = 0x21 if args[2] == 'S' else 0x22

            cmd = struct.pack('BB', direction, number)
            marklin_sock.send(cmd)
        elif name == 'dump':
            assert len(args) == 1
            marklin_sock.send('\x85')
            result = marklin_sock.recv(10, socket.MSG_WAITALL)
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
        elif name == 'time':
            print "Time %d (%f s)" % (state.time, state.time / 100.0)
