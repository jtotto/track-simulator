import socket
import sys
import time

import numpy as np
import numpy.random
from numpy.random import randint

import graph_tool.draw
from graph_tool import Graph

from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

import cs452_track.track as cs452_track

# Wait this long before responding delivering the respones to a sensor poll.
POLL_TICKS = 5

class MarklinController:
    def __init__(self):
        self.buf = ''
        self.sensors = bytearray(10)

    def feed(self, data):
        self.buf += data

    def interpret(self):
        if len(self.buf) < 1:
            return None,

        f = ord(self.buf[0])
        if 0 <= f <= 14:
            if len(self.buf) < 2:
                return None,
            print "Setting speed of %d to %d" % (ord(self.buf[1]), f)
            train = ord(self.buf[1])
            self.buf = self.buf[2:]
            return 'set_speed', train, f
        elif f == 15:
            if len(self.buf) < 2:
                return None,
            train = ord(self.buf[1])
            print "Toggling reverse of %d" % train
            self.buf = self.buf[2:]
            return 'toggle_reverse', train
        elif f == 0x20:
            print "Disabling solenoid"
            self.buf = self.buf[1:]
            return 'disable_solenoid',
        elif 0x21 <= f <= 0x22:
            if len(self.buf) < 2:
                return None,
            switchno = ord(self.buf[1])
            self.buf = self.buf[2:]
            return 'switch', switchno, f - 0x21
        elif f == 0x85:
            sensor_snapshot = self.sensors
            self.sensors = bytearray(10)
            self.buf = self.buf[1:]
            return 'sensor', sensor_snapshot
        elif f == 0xc0:
            print "Sensor dump mode change"
            self.buf = self.buf[1:]
            return 'sensor_dump_mode',

        raise Exception("Unknown command %s" % self.buf.encode('hex'))

    def set_sensor_tripped(self, num):
        self.set_sensor_is_tripped(num, True)

    def set_sensor_is_tripped(self, num, tripped):
        word_num = num / 8
        offset = 7 - num % 8
        bit = 0x1 << offset

        assert word_num < len(self.sensors)
        mask = self.sensors[word_num]
        print 'Sensor!', num, tripped, word_num, offset, bit, mask
        mask = (mask | bit) if tripped else (mask & ~bit)
        self.sensors[word_num] = mask

class Track:
    # Create the CS452 track graph and from that the visual graph_tool track
    # graph.
    def __init__(self):
        self.tr = cs452_track.init_tracka()
        self.g = g = Graph()

        g.add_vertex(len(self.tr))

        for (vi, node) in enumerate(self.tr):
            node.i = vi

        n_title = g.new_vertex_property("string")
        n_colour = g.new_vertex_property("string")
        e_title = g.new_edge_property("string")
        self.e_pen_width = e_pen_width = g.new_edge_property("float")
        self.e_start_marker = e_start_marker = g.new_edge_property("int")

        # This is the definition of the embedding as 2D coordinates, which we
        # need to hold on to for use in later drawing operations.
        self.n_pos = n_pos = g.new_vertex_property("vector<double>")

        # This maps edges in the graph_tool graph to their physical distances.
        # We don't use it directly in drawing, but we need it to scale other
        # drawing operations properly so we hold on to it.
        self.e_dist = e_dist = g.new_edge_property("double")

        colours = {
            cs452_track.NODE_SENSOR: 'blue',
            cs452_track.NODE_BRANCH: 'orange',
            cs452_track.NODE_MERGE: 'yellow',
            cs452_track.NODE_ENTER: 'green',
            cs452_track.NODE_EXIT: 'red',
        }

        for node in self.tr:
            v = g.vertex(node.i)
            n_title[v] = node.name
            if node.typ == cs452_track.NODE_EXIT:
                n_pos[v] = (node.reverse.coord_x, node.reverse.coord_y)
            else:
                n_pos[v] = (node.coord_x, node.coord_y)
            e = g.add_edge(g.vertex(node.i), g.vertex(node.reverse.i))

            n_colour[v] = colours[node.typ]

            for i, edge in enumerate(node.edge):
                if edge.src is None:
                    continue

                e = g.add_edge(g.vertex(edge.src.i), g.vertex(edge.dest.i))

                if node.typ == cs452_track.NODE_BRANCH \
                    and node.switch_direction == i:
                    e_start_marker[e] = 1
                else:
                    e_start_marker[e] = 0

                e_dist[e] = edge.dist
                e_pen_width[e] = 1.0
                e_title[e] = "%.2f" % (edge.dist)

        self.win = graph_tool.draw.GraphWindow(g,
                                               n_pos,
                                               (1280, 960),
                                               edge_text=e_title,
                                               edge_pen_width=e_pen_width,
                                               vertex_fill_color=n_colour,
                                               vertex_text=n_title,
                                               vertex_font_size=6,
                                               edge_start_marker=e_start_marker,
                                               edge_end_marker='none',
                                               edge_font_size=6)

    def show(self):
        self.win.show_all()

    def set_switch(self, sw, d):
        for node in self.tr:
            if node.typ == cs452_track.NODE_BRANCH and node.num == sw:
                node.switch_direction = d

                for i, edge in enumerate(node.edge):
                    e = self.g.edge(self.g.vertex(edge.src.i), self.g.vertex(edge.dest.i))
                    self.e_start_marker[e] = 1 \
                        if node.switch_direction == i \
                        else 0

        print "WARN: Could not find switch %d" % sw

class Train:
    STOPPED, ACCELERATING, DECELERATING, CONSTANT_VELOCITY = range(4)
    motion_names = {
        STOPPED: 'STOPPED',
        ACCELERATING: 'ACCELERATING',
        DECELERATING: 'DECELERATING',
        CONSTANT_VELOCITY: 'CONSTANT_VELOCITY',
    }

    def __init__(self, track, marklin, num):
        self.track = track
        self.num = num
        self.v = 0.0
        self.speed = 0
        self.marklin = marklin

        self.motion = Train.STOPPED

        # Super simple for now. mm/s^2
        self.accl = 100.0
        # mm/s
        self.cvs = {
            0: 0.0,
            8: 350.0,
            10: 400.0,
            12: 450.0,
            14: 500.0
        }

        # Our train's current position on the track graph.
        self.edge = track.tr[0].edge[0]
        self.edge_dist = 0

    def dump_state(self):
        return 'Train %d: at (%s+%d), speed %d, %s, velocity %f mm/s' % (
            self.num,
            self.edge.src.name,
            self.edge_dist,
            self.speed,
            Train.motion_names[self.motion],
            self.v)

    # You WILL shoot your foot off trying to modify this code without careful
    # unit analysis.  Consider yourself warned.
    def advance(self):
        # Time advances by one tick.  What do we do?
        if self.motion == Train.STOPPED:
            return
        elif self.motion == Train.ACCELERATING:
            self.v += self.accl / 100.0
            if self.v >= self.cvs[self.speed]:
                self.v = self.cvs[self.speed]
                self.motion = Train.CONSTANT_VELOCITY
        elif self.motion == Train.DECELERATING:
            self.v -= self.accl / 100.0
            if self.v <= self.cvs[self.speed]:
                self.v = self.cvs[self.speed]
                self.motion = Train.STOPPED if self.speed == 0 \
                                            else Train.CONSTANT_VELOCITY

        self.edge_dist += self.v / 100.0
        while True:
            e = self.e()
            if self.edge_dist < self.track.e_dist[e]:
                break
            if self.edge.dest.typ == cs452_track.NODE_SENSOR:
                self.marklin.set_sensor_tripped(self.edge.dest.num)
            self.edge = self.edge.dest.edge[self.edge.dest.switch_direction]
            self.edge_dist -= self.track.e_dist[e]

    def set_speed(self, speed):
        assert speed in self.cvs
        if speed > self.speed:
            self.motion = Train.ACCELERATING
        elif speed < self.speed:
            self.motion = Train.DECELERATING

        self.speed = speed

    def toggle_reverse(self):
        if not self.motion == Train.STOPPED:
            print 'Unsafe reversal while not stopped!'

        self.edge = self.edge.reverse
        self.edge_dist = self.track.e_dist[self.e()] - self.edge_dist

    # Map our current edge in the CS452 track graph to the graph_tool visual
    # track graph.
    def e(self):
        return self.track.g.edge(self.edge.src.i, self.edge.dest.i)

    # Render our current position on the graph window.
    def draw(self, da, cr):
        e = self.e()
        start = np.array(self.track.n_pos[e.source()])
        end = np.array(self.track.n_pos[e.target()])
        alpha = self.edge_dist / self.track.e_dist[e]
        pos = start + alpha * (end - start)
        dp = self.track.win.graph.pos_to_device(pos) # dp: device position
        cr.rectangle(dp[0]-10, dp[1]-10, 20, 20)
        cr.set_source_rgb(102. / 256, 102. / 256, 102. / 256)
        cr.fill()
        cr.move_to(dp[0]-10, dp[1] + 10 - 12./2)
        cr.set_source_rgb(1., 1., 1.)
        cr.set_font_size(12)
        cr.show_text("%d" % self.num)
        cr.fill()

def simulator(marklin_sock, timer_sock, tns):
    # The logical components of the simulator are:
    #   - The track, which is essentially the awful track graph representation
    #     from the course page with some additional state.
    #   - The trains, each of which maintains information about its own position
    #     on and state of motion around the track.
    #   - The mock serial interface, which is the glue between the simulated
    #     kernel and the simulated track.
    #
    # The state of the simulator changes in response to three types of events:
    #   - Key presses, which signal the advancement of time by a single step.
    #   - Track widget re-draws, which refresh the visual display with the
    #     current state of the trains and track.
    #   - Availability of new data from the mock serial interface.

    track = Track()
    marklin = MarklinController()
    trains = { tn: Train(track, marklin, tn) for tn in tns }

    # Various bits of simple state we still need to be mutable from the
    # closures.
    class Simulator:
        def __init__(self):
            self.time = 1
            self.last_polled = -1
            self.poll_snapshot = None

    sim = Simulator()

    def marklin_sock_readable(source, condition):
        assert source == marklin_sock and condition == GLib.IO_IN
        data = marklin_sock.recv(1024)
        if not data:
            return True

        marklin.feed(data)
        while True:
            cmd = marklin.interpret()
            if cmd[0] is None:
                return True
            elif cmd[0] == 'set_speed':
                tn, speed = cmd[1:]
                trains[tn].set_speed(speed)
            elif cmd[0] == 'toggle_reverse':
                tn = cmd[1]
                trains[tn].toggle_reverse()
            elif cmd[0] == 'switch':
                switchno, direction = cmd[1:]
                track.set_switch(switchno, direction)
            elif cmd[0] == 'sensor':
                assert sim.poll_snapshot is None
                sim.last_polled = sim.time
                sim.poll_snapshot = cmd[1]
            else:
                print "Ignoring command %s" % cmd[0]

    def draw_simulation(da, cr):
        cr.move_to(10.0, 10.0)
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.set_font_size(12)
        time_text = "Time %d (%f s), Last polled at %d" % (
            sim.time, sim.time / 100.0, sim.last_polled)
        cr.show_text(time_text)
        _, _, _, text_height, _, _ = cr.text_extents(time_text)
        y = 10.0 + text_height
        for train in trains.values():
            train.draw(da, cr)
            state_text = train.dump_state()
            cr.move_to(10.0, y)
            cr.set_source_rgb(0.0, 0.0, 0.0)
            cr.set_font_size(12)
            cr.show_text(state_text)
            _, _, _, text_height, _, _ = cr.text_extents(state_text)
            y += text_height


    def destroy_callback(*args, **kwargs):
        track.win.destroy()
        Gtk.main_quit()

    def key_callback(widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        steps = 10 if keyname == 'n' else 1

        for i in xrange(steps):
            sim.time += 1
            if sim.poll_snapshot is not None \
                and sim.time > sim.last_polled + POLL_TICKS:
                sent = marklin_sock.send(sim.poll_snapshot)
                # There's no way we're going to fill the send buffer...
                assert sent == len(sim.poll_snapshot)
                sim.poll_snapshot = None

            for train in trains.values():
                train.advance()

            timer_sock.send('0')

        track.win.graph.regenerate_surface()
        track.win.graph.queue_draw()

    track.win.connect("delete_event", destroy_callback)
    track.win.connect('key-press-event', key_callback)
    track.win.graph.connect("draw", draw_simulation)
    GLib.io_add_watch(marklin_sock, GLib.IO_IN, marklin_sock_readable)

    track.show()

    Gtk.main()

if __name__ == '__main__':
    tns = [int(arg) for arg in sys.argv[1:]]
    if not tns:
        print 'Usage: simulator.py [train number]*'
        sys.exit()

    marklin_sock = socket.socket(socket.AF_UNIX)
    marklin_sock.bind('\0marklin-simulator')
    marklin_sock.listen(0)

    timer_sock = socket.socket(socket.AF_UNIX)
    timer_sock.bind('\0timer-simulator')
    timer_sock.listen(0)

    marklin_client, addr = marklin_sock.accept()
    marklin_client.setblocking(0)

    timer_client, addr = timer_sock.accept()
    timer_client.setblocking(0)

    simulator(marklin_client, timer_client, tns)
