#!/usr/bin/env python

'''
Transmit beacon request frames to the broadcast address while
channel hopping to identify ZigBee Coordinator/Router devices.
'''

import sys
import os
import signal
import time
import argparse
import threading
from gps import *

from killerbee import *

txcount = 0
rxcount = 0
stumbled = {}

class GpsPoller(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.session = gps(mode=WATCH_ENABLE)
        self.current_value = None
        self.running=True

    def get_current_value(self):
        return self.current_value

    def run(self):
        try:
            while self.running:
                self.current_value = self.session.next()
        except StopIteration:
            pass

def display_details(routerdata):
    global args, csvfile
    stackprofile_map = {0:"Network Specific",
            1:"ZigBee Standard",
            2:"ZigBee Enterprise"}
    stackver_map = {0:"ZigBee Prototype",
            1:"ZigBee 2004",
            2:"ZigBee 2006/2007"}
    spanid, source, extpanid, stackprofilever, channel = routerdata
    stackprofile = ord(stackprofilever) & 0x0f
    stackver = (ord(stackprofilever) & 0xf0) >>4

    print "New Network: PANID 0x%02X%02X  Source 0x%02X%02X"%(ord(spanid[0]), ord(spanid[1]), ord(source[0]), ord(source[1]))

    try:
        extpanidstr=""
        for ind in range(0,7):
            extpanidstr += "%02x:"%ord(extpanid[ind])
        extpanidstr += "%02X"%ord(extpanid[-1])
        sys.stdout.write("\tExt PANID: " + extpanidstr)
    except IndexError:
        sys.stdout.write("\tExt PANID: Unknown")

    try:
        print "\tStack Profile: %s"%stackprofile_map[stackprofile]
        stackprofilestr = stackprofile_map[stackprofile]
    except KeyError:
        print "\tStack Profile: Unknown (%d)"%stackprofile
        stackprofilestr = "Unknown (%d)"%stackprofile

    try:
        print("\tStack Version: {0}".format(stackver_map[stackver]))
        stackverstr = stackver_map[stackprofile]
    except KeyError:
        print("\tStack Version: Unknown ({0})".format(stackver))
        stackverstr = "Unknown (%d)"%stackver

    print("\tChannel: {0}".format(channel))

    if args.gps:
        # Get current GPS location.
        loc = gpsp.get_current_value()
        

    if args.csvfile is not None:
        csvfile.write("0x%02X%02X,0x%02X%02X,%s,%s,%s,%d\n"%(ord(spanid[0]), ord(spanid[1]), ord(source[0]), ord(source[1]), extpanidstr, stackprofilestr, stackverstr, channel))

def response_handler(stumbled, packet, channel):
    global args
    d154 = Dot154PacketParser()
    # Chop the packet up
    pktdecode = d154.pktchop(packet)

    # Byte-swap the frame control field
    fcf = struct.unpack("<H", pktdecode[0])[0]

    # Check if this is a beacon frame
    if (fcf & DOT154_FCF_TYPE_MASK) == DOT154_FCF_TYPE_BEACON:
        if args.verbose:
            print "Received frame is a beacon."

        # The 6th element offset in the Dot154PacketParser.pktchop() method
        # contains the beacon data in its own list.  Extract the Ext PAN ID.
        spanid = pktdecode[4][::-1]
        source = pktdecode[5][::-1]
        beacondata = pktdecode[6]
        extpanid = beacondata[6][::-1]
        stackprofilever = beacondata[4]

        key = ''.join([spanid, source])
        value = [spanid, source, extpanid, stackprofilever, channel]
        if not key in stumbled:
            if args.verbose:
                print("Beacon represents new network.")
                #print hexdump(packet)
                #print pktdecode
            stumbled[key] = value
            display_details(value)
        return value

    if args.verbose:
        print("Received frame is not a beacon (FCF={0}).".format(pktdecode[0].encode('hex')))

    return None

def interrupt(signum, frame):
    global txcount, rxcount
    global kb
    global args, csvfile
    global gpsp

    if args.csvfile is not None:
        csvfile.close()
    kb.close()
    print("\n{0} packets transmitted, {1} responses.".format(txcount, rxcount))
    if args.gps:
        print("\nKilling GPS poller thread.")
        gpsp.running = False
        gpsp.join()
    sys.exit(0)

if __name__ == '__main__':
    # Command-line arguments
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--iface', '--dev', action='store', dest='devstring')
    parser.add_argument('-s', '--delay', action='store', type=float, dest='delay', default=2.0)
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-c', '--channel', action='store', type=int, default=None)
    parser.add_argument('-w', '--file', action='store', dest='csvfile', default=None)
    parser.add_argument('-g', '--gps', action='store_true')
    parser.add_argument('-D', action='store_true', dest='showdev')
    args = parser.parse_args()

    if args.showdev:
        show_dev()
        sys.exit(0)

    if args.csvfile is not None:
        try:
            csvfile = open(args.csvfile, 'w')
        except Exception as e:
            print("Issue opening CSV output file: {0}.".format(e))
        csvfile.write("panid,source,extpanid,stackprofile,stackversion,channel\n")

    if args.gps:
        gpsp = GpsPoller()
        try:
            if args.verbose:
                print("Starting GPS Poller.")
            gpsp.start()
        except:
            print("GPS Poller Error")
            sys.exit(-1)

    # Beacon frame
    beacon = "\x03\x08\x00\xff\xff\xff\xff\x07"
    # Immutable strings - split beacon around sequence number field
    beaconp1 = beacon[0:2]
    beaconp2 = beacon[3:]

    try:
        kb = KillerBee(device=args.devstring)
    except KBInterfaceError as e:
        print("Interface Error: {0}".format(e))
        sys.exit(-1)

    signal.signal(signal.SIGINT, interrupt)
    print("zbstumbler: Transmitting and receiving on interface \'{0}\'".format(kb.get_dev_info()[0]))

    # Sequence number of beacon request frame
    seqnum = 0
    if args.channel:
        channel = args.channel
        kb.set_channel(channel)
    else:
        channel = 11

    # Loop injecting and receiving packets
    while 1:
        if channel > 26:
            channel = 11

        if seqnum > 255:
            seqnum = 0

        if not args.channel:
            if args.verbose:
                print("Setting channel to {0}.".format(channel))
            try:
                kb.set_channel(channel)
            except Exception, e:
                print("ERROR: Failed to set channel to {0}. ({1})".format(channel, e))
                sys.exit(-1)

        if args.verbose:
            print("Transmitting beacon request.")

        beaconinj = ''.join([beaconp1, "%c" % seqnum, beaconp2])

        # Process packets for arg_delay seconds looking for the beacon
        # response frame.
        start = time.time()

        try:
            txcount+=1
            kb.inject(beaconinj)
        except Exception, e:
            print("ERROR: Unable to inject packet: {0}".format(e))
            sys.exit(-1)

        while (start+args.delay > time.time()):
            # Does not block
            recvpkt = kb.pnext()
            # Check for empty packet (timeout) and valid FCS
            if recvpkt != None and recvpkt[1]:
                rxcount += 1
                if args.verbose:
                    print("Received frame.")#, time.time()-start
                networkdata = response_handler(stumbled, recvpkt[0], channel) 

        kb.sniffer_off()    
        seqnum += 1
        if not args.channel:
            channel += 1

