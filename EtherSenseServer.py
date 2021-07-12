#!/usr/bin/python
import pyrealsense2 as rs
import sys, getopt
import asyncore
import numpy as np
import pickle
import socket
import struct
import cv2


print('Number of arguments:', len(sys.argv), 'arguments.')
print('Argument List:', str(sys.argv))
#mc_ip_address = '224.0.0.1'
mc_ip_address = 'localhost'
port = 1024
chunk_size = 4096
clipping_distance_in_meters = 2.0
camera_width = 640
camera_height = 360
#rs.log_to_console(rs.log_severity.debug)


def getColorDepthTimestamp(pipeline, color_depth_filter, align, depth_sensor):
    frames = pipeline.wait_for_frames()
    # take owner ship of the frame for further processing
    #frames.keep()
    aligned_frames = align.process(frames)
    color = aligned_frames.get_color_frame()
    depth = aligned_frames.get_depth_frame()

    depth_scale = depth_sensor.get_depth_scale()
    clipping_distance = clipping_distance_in_meters / depth_scale

    if color and depth:
        # represent the frame as a numpy array
        colorData = color.get_data()
        depthData = depth.get_data()
        colorMat = np.asanyarray(colorData)
        depthMat = np.asanyarray(depthData)
        depthMat = np.where((depthMat > clipping_distance) | (depthMat <= 0), 0, depthMat)
        ts = frames.get_timestamp()

        return colorMat, depthMat, ts
    else:
        return None, None, None

def openPipeline():
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, camera_width, camera_height, rs.format.z16, 30)
    cfg.enable_stream(rs.stream.color, camera_width, camera_height, rs.format.bgr8, 30)
    pipeline = rs.pipeline()
    pipeline_profile = pipeline.start(cfg)
    align = rs.align(rs.stream.color)
    sensor = pipeline_profile.get_device().first_depth_sensor()
    intr = pipeline_profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()

    return pipeline, align, intr, sensor

class DevNullHandler(asyncore.dispatcher_with_send):

    def handle_read(self):
        print(self.recv(1024))

    def handle_close(self):
        self.close()


class EtherSenseServer(asyncore.dispatcher):
    def __init__(self, address):
        asyncore.dispatcher.__init__(self)
        print("Launching Realsense Camera Server")
        try:
            self.pipeline, self.align, intr, self.depth_sensor = openPipeline()
            self.intr = np.asanyarray([intr.width, intr.height, intr.fx, intr.fy, intr.ppx, intr.ppy])
        except:
            print("Unexpected error: ", sys.exc_info()[1])
            sys.exit(1)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        print('sending acknowledgement to', address)

        # reduce the resolution of the depth image using post processing
        self.decimate_filter = rs.decimation_filter()
        self.decimate_filter.set_option(rs.option.filter_magnitude, 2)
        self.frame_data = ''
        self.connect((address[0], 1024))
        self.packet_id = 0


    def handle_connect(self):
        print("connection received")


    def writable(self):
        return True


    def update_frame(self):
        color, depth, timestamp = getColorDepthTimestamp(self.pipeline, self.decimate_filter, self.align, self.depth_sensor)
        if (color is not None) and (depth is not None):
            # convert the depth image to a string for broadcast
            data = pickle.dumps([color, depth, self.intr])
            #print(color,depth)
            # capture the lenght of the data portion of the message
            length = struct.pack('<I', len(data))
            # include the current timestamp for the frame
            ts = struct.pack('<d', timestamp)
            # for the message for transmission
            self.frame_data = length + ts + data


    def handle_write(self):
        # first time the handle_write is called
        if not hasattr(self, 'frame_data'):
            self.update_frame()
        # the frame has been sent in it entirety so get the latest frame
        if len(self.frame_data) == 0:
            self.update_frame()
        else:
            # send the remainder of the frame_data until there is no data remaining for transmition
            remaining_size = self.send(self.frame_data)
            self.frame_data = self.frame_data[remaining_size:]


    def handle_close(self):
        self.close()


class MulticastServer(asyncore.dispatcher):
    def __init__(self, host = mc_ip_address, port=1024):
        asyncore.dispatcher.__init__(self)
        server_address = ('', port)
        self.create_socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.bind(server_address)

    def handle_read(self):
        data, addr = self.socket.recvfrom(42)
        print('Recived Multicast message %s bytes from %s' % (data, addr))
        # Once the server recives the multicast signal, open the frame server
        EtherSenseServer(addr)
        print(sys.stderr, data)

    def writable(self):
        return False # don't want write notifies

    def handle_close(self):
        self.close()

    def handle_accept(self):
        channel, addr = self.accept()
        print('received %s bytes from %s' % (data, addr))


def main(argv):
    # initalise the multicast receiver 
    server = MulticastServer()
    # hand over excicution flow to asyncore
    asyncore.loop()

if __name__ == '__main__':
    main(sys.argv[1:])

