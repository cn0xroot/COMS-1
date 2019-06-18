"""
xrit-demux.py
https://github.com/sam210723/COMS-1

Frontend for CCSDS demultiplexer
"""

from argparse import ArgumentParser
from configparser import ConfigParser
from demuxer import Demuxer
from os import mkdir, path
import socket
from time import time

# Globals
args = None
config = None
stime = None
source = None
downlink = None
output = None
packetf = None
sck = None
buflen = 892
demux = None

def init():
    print("┌───────────────────────────────────┐")
    print("│        COMS-1 xRIT Demuxer        │")
    print("│    github.com/sam210723/COMS-1    │")
    print("└───────────────────────────────────┘\n")
    
    global args
    global config
    global stime
    global demux

    # Handle arguments and config file
    args = parse_args()
    config = parse_config(args.config)
    print_config()

    # Configure directories and input source
    dirs()
    config_input()

    # Create demuxer instance
    demux = Demuxer(downlink)

    # Check demuxer thread is ready
    if not demux.coreReady:
        print("DEMUXER CORE THREAD FAILED TO START\nExiting...")
        exit()

    print("──────────────────────────────────────────────────────────────────────────────────\n")

    # Get processing start time
    stime = time()

    # Enter main loop
    loop()


def loop():
    """
    Handles data from the selected input source
    """
    global demux
    global source
    global sck
    global buflen

    while True:
        if source == "OSP":
            data = sck.recv(buflen)
            demux.push(data)
        
        elif source == "GOESRECV":
            data = sck.recv(buflen + 8)

            if len(data) == buflen + 8:
                demux.push(data[8:])

        elif source == "FILE":
            global packetf
            global stime

            if not packetf.closed:
                # Read VCDU from file
                data = packetf.read(buflen)

                # No more data to read from file
                if data == b'':
                    print("INPUT FILE LOADED")
                    packetf.close()
                    continue
                
                # Push VCDU to demuxer
                demux.push(data)
            else:
                # Demuxer has all VCDUs from file, wait for processing
                if demux.complete():
                    runTime = round(time() - stime, 3)
                    print("FINISHED PROCESSING FILE ({}s)\nExiting...".format(runTime))
                    
                    # Stop core thread
                    demux.stop()
                    exit()


def config_input():
    """
    Configures the selected input source
    """

    global source
    global sck

    if source == "OSP":
        sck = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        ip = config.get('osp', 'ip')
        port = int(config.get('osp', 'vchan'))
        addr = (ip, port)

        print("Connecting to Open Satellite Project ({})...".format(ip), end='')
        connect_socket(addr)

    elif source == "GOESRECV":
        sck = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        ip = config.get('goesrecv', 'ip')
        port = int(config.get('goesrecv', 'vchan'))
        addr = (ip, port)

        print("Connecting to goesrecv ({})...".format(ip), end='')
        connect_socket(addr)
        nanomsg_init()

    elif source == "FILE":
        global packetf
        packetf = open(args.file, 'rb')
        print("Opened file: \"{}\"".format(args.file))

    else:
        print("UNKNOWN INPUT MODE: \"{}\"".format(source))
        print("Exiting...")
        exit()


def connect_socket(addr):
    """
    Connects TCP socket to address and handle exceptions
    """

    try:
        sck.connect(addr)
        print("CONNECTED")
    except socket.error as e:
        if e.errno == 10061:
            print("CONNECTION REFUSED")
        else:
            print(e)
    
        print("\nExiting...")
        exit()


def nanomsg_init():
    """
    Sets up nanomsg publisher in goesrecv to send VCDUs over TCP
    """

    global sck

    sck.send(b'\x00\x53\x50\x00\x00\x21\x00\x00')
    nmres = sck.recv(8)

    # Check nanomsg response
    if nmres != b'\x00\x53\x50\x00\x00\x20\x00\x00':
        print("  ERROR CONFIGURING NANOMSG (BAD RESPONSE)\n  Exiting...\n")
        exit()


def dirs():
    """
    Configures directories for demuxed files
    """

    paths = [
        output,
        output + "/LRIT",
        output + "/LRIT/IMG",
        output + "/LRIT/IMG/FD",
        output + "/LRIT/IMG/ENH",
        output + "/LRIT/IMG/LSH",
        output + "/LRIT/ADD",
        output + "/LRIT/ADD/ANT",
        output + "/LRIT/ADD/GOCI",
        output + "/LRIT/ADD/NWP",
        output + "/LRIT/ADD/TYP",
    ]

    print()
    # Loop through paths in list
    for p in paths:
        absp = path.abspath(p)
        
        # Create new directory if it doesn't exist already
        if not path.isdir(absp):
            try:
                mkdir(absp)
                print("Created: {}".format(p))
            except OSError as e:
                print("Error creating output folders\n{}\n\nExiting...".format(e))
                exit()


def parse_args():
    """
    Parses command line arguments
    """

    argp = ArgumentParser()
    argp.description = "Frontend for CCSDS demultiplexer"
    argp.add_argument("--config", action="store", help="Configuration file path (.ini)", default="xrit-demux.ini")
    argp.add_argument("--file", action="store", help="Path to VCDU packet file", default=None)
    
    return argp.parse_args()


def parse_config(path):
    """
    Parses configuration file
    """

    global source
    global downlink
    global output

    cfgp = ConfigParser()
    cfgp.read(path)

    if args.file == None:
        source = cfgp.get('demuxer', 'input').upper()
    else:
        source = "FILE"
    
    downlink = cfgp.get('demuxer', 'mode').upper()
    output = cfgp.get('demuxer', 'output')

    return cfgp


def print_config():
    """
    Prints configuration information
    """

    global downlink
    global output

    print("SPACECRAFT:       COMS-1")

    if downlink == "LRIT":
        rate = "64 kbps"
    elif downlink == "HRIT":
        rate = "3 Mbps"
    print("DOWNLINK:         {} ({})".format(downlink, rate))

    if source == "OSP":
        s = "Open Satellite Project (github.com/opensatelliteproject/xritdemod)"
    elif source == "GOESRECV":
        s = "goesrecv (github.com/pietern/goestools)"
    elif source == "FILE":
        s = "File ({})".format(args.file)
    else:
        s = "UNKNOWN"

    print("INPUT SOURCE:     {}".format(s))
    
    absp = path.abspath(output)
    absp = absp[0].upper() + absp[1:]  # Fix lowercase drive letter
    print("OUTPUT PATH:      {}".format(absp))


try:
    init()
except KeyboardInterrupt:
    demux.stop()
    print("Exiting...")
    exit()
