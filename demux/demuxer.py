"""
demuxer.py
https://github.com/sam210723/COMS-1
"""

import ccsds as CCSDS
from collections import deque
from time import sleep
from threading import Thread
from tools import CCITT_LUT

class Demuxer:
    """
    Coordinates demultiplexing of CCSDS virtual channels into xRIT files.
    """

    def __init__(self, downlink, v, d, output, k):
        """
        Initialises demuxer class
        """
        
        # Configure instance globals
        self.rxq = deque()              # Data receive queue
        self.coreReady = False          # Core thread ready state
        self.coreStop = False           # Core thread stop flag
        self.verbose = v                # Verbose output flag
        self.dumpPath = d               # VCDU dump file path
        self.outputPath = output        # xRIT file output path root
        self.keys = k                   # Decryption keys
        self.channelHandlers = {}       # List of channel handlers
        self.vcduCounter = -1           # VCDU continuity counter

        if downlink == "LRIT":
            self.coreWait = 54          # Core loop delay in ms for LRIT (108.8ms per packet @ 64 kbps)
        elif downlink == "HRIT":
            self.coreWait = 1           # Core loop delay in ms for HRIT (2.2ms per packet @ 3 Mbps)

        # Start core demuxer thread
        demux_thread = Thread()
        demux_thread.name = "DEMUX CORE"
        demux_thread.run = self.demux_core
        demux_thread.start()

    def demux_core(self):
        """
        Distributes VCDUs to channel handlers.
        """
        
        # Indicate core thread has initialised
        self.coreReady = True

        # Thread globals
        lastVCID = None             # Last VCID seen
        crclut = CCITT_LUT()        # CP_PDU CRC LUT

        # Open VCDU dump file
        dumpFile = None
        if self.dumpPath != None:
            dumpFile = open(self.dumpPath, 'wb+')

        # Thread loop
        while not self.coreStop:
            # Pull next packet from queue
            packet = self.pull()
            
            # If queue is not empty
            if packet != None:
                # Parse VCDU
                vcdu = CCSDS.VCDU(packet)

                # Dump raw VCDU to file
                if dumpFile != None and vcdu.VCID != 63:
                    dumpFile.write(packet)

                # Check spacecraft is supported
                if vcdu.SC != "COMS-1":
                    if self.verbose: print("SPACECRAFT \"{}\" NOT SUPPORTED".format(vcdu.SCID))
                    continue

                # Check VCDU continuity counter
                self.continuity(vcdu)

                # Check for VCID change
                if lastVCID != vcdu.VCID:
                    if self.verbose: print()
                    vcdu.print_info()
                    lastVCID = vcdu.VCID

                # Discard fill packets
                if vcdu.VCID == 63:
                    continue
                
                # Check channel handler for current VCID exists
                try:
                    self.channelHandlers[vcdu.VCID]
                except KeyError:
                    # Create new channel handler instance
                    self.channelHandlers[vcdu.VCID] = Channel(vcdu.VCID, self.verbose, crclut, self.outputPath, self.keys)
                    if self.verbose: print("  CREATED NEW CHANNEL HANDLER\n")

                # Pass VCDU to appropriate channel handler
                self.channelHandlers[vcdu.VCID].data_in(vcdu)
                
            else:
                # No packet available, sleep thread
                sleep(self.coreWait / 1000)
        
        # Gracefully exit core thread
        if self.coreStop:
            if dumpFile != None:
                dumpFile.close()
            
            return

    def continuity(self, vcdu):
        """
        Checks VCDU packet continuity by comparing packet counters
        """

        # If at least one VCDU has been received
        if self.vcduCounter != -1:
            # Check counter reset
            if self.vcduCounter == 16777215 and vcdu.COUNTER == 0:
                self.vcduCounter = vcdu.COUNTER
                return
            
            diff = vcdu.COUNTER - self.vcduCounter - 1
            if diff != 0:
                if self.verbose:
                    print("  DROPPED {} PACKETS    (CURRENT: {}   LAST: {}   VCID: {})".format(diff, vcdu.COUNTER, self.vcduCounter, vcdu.VCID))
                else:
                    print("  DROPPED {} PACKETS".format(diff))
        
        self.vcduCounter = vcdu.COUNTER

    def push(self, packet):
        """
        Takes in VCDUs for the demuxer to process
        :param packet: 892 byte Virtual Channel Data Unit (VCDU)
        """

        self.rxq.append(packet)

    def pull(self):
        """
        Pull data from receive queue
        """

        try:
            # Return top item
            return self.rxq.popleft()
        except IndexError:
            # Queue empty
            return None

    def complete(self):
        """
        Checks if receive queue is empty
        """

        if len(self.rxq) == 0:
            return True
        else:
            return False

    def stop(self):
        """
        Stops the demuxer loop by setting thread stop flag
        """

        self.coreStop = True


class Channel:
    """
    Virtual channel data handler
    """

    def __init__(self, vcid, v, crclut, output, k):
        """
        Initialises virtual channel data handler
        :param vcid: Virtual Channel ID
        :param v: Verbose output flag
        :param crclut: CP_PDU CRC LUT
        :param output: xRIT file output path root
        :param k: Decryption keys
        """

        self.VCID = vcid            # VCID for this handler
        self.verbose = v            # Verbose output flag
        self.crclut = crclut        # CP_PDU CRC LUT
        self.outputPath = output    # xRIT file output path root
        self.keys = k               # Decryption keys
        self.cCPPDU = None          # Current CP_PDU object
        self.cTPFile = None         # Current TP_File object


    def data_in(self, vcdu):
        """
        Takes in VCDUs for the channel handler to process
        :param packet: Parsed VCDU object
        """

        # Parse M_PDU
        mpdu = CCSDS.M_PDU(vcdu.MPDU)

        # If M_PDU contains CP_PDU header
        if mpdu.HEADER:
            # If data preceeds header
            if mpdu.POINTER != 0:
                # Finish previous CP_PDU
                preptr = mpdu.PACKET[:mpdu.POINTER]

                try:
                    lenok, crcok = self.cCPPDU.finish(preptr, self.crclut)
                    if self.verbose: self.check_CPPDU(lenok, crcok)

                    # Handle finished CP_PDU
                    self.handle_CPPDU(self.cCPPDU)
                except AttributeError:
                    if self.verbose: print("  NO CP_PDU TO FINISH (DROPPED PACKETS?)")

                #TODO: Check CP_PDU continuity

                # Create new CP_PDU
                postptr = mpdu.PACKET[mpdu.POINTER:]
                self.cCPPDU = CCSDS.CP_PDU(postptr)

                # Handle CP_PDUs less than one M_PDU in length
                if 1 < self.cCPPDU.LENGTH < 886 and len(self.cCPPDU.PAYLOAD) > self.cCPPDU.LENGTH:
                    # Remove trailing null bytes (M_PDU padding)
                    self.cCPPDU.PAYLOAD = self.cCPPDU.PAYLOAD[:self.cCPPDU.LENGTH]
                    
                    try:
                        lenok, crcok = self.cCPPDU.finish(b'', self.crclut)
                        if self.verbose: self.check_CPPDU(lenok, crcok)

                        # Handle finished CP_PDU
                        self.handle_CPPDU(self.cCPPDU)
                    except AttributeError:
                        if self.verbose: print("  NO CP_PDU TO FINISH (DROPPED PACKETS?)")

            else:
                # First CP_PDU in TP_File
                # Create new CP_PDU
                self.cCPPDU = CCSDS.CP_PDU(mpdu.PACKET)

            # Handle special EOF CP_PDU
            if self.cCPPDU.is_EOF():
                self.cCPPDU = None
                if self.verbose: print("  [CP_PDU] EOF MARKER")
            else:
                if self.verbose:
                    self.cCPPDU.print_info()
                    print("    HEADER:     0x{}".format(hex(mpdu.POINTER)[2:].upper()))
        else:
            # Append packet to current CP_PDU
            try:
                self.cCPPDU.append(mpdu.PACKET)
            except AttributeError:
                if self.verbose: print("  NO CP_PDU TO APPEND M_PDU TO (DROPPED PACKETS?)")

    
    def check_CPPDU(self, lenok, crcok):
        """
        Checks length and CRC of finished CP_PDU
        """

        # Show length error
        if lenok:
            print("    LENGTH:     OK")
        else:
            ex = self.cCPPDU.LENGTH
            ac = len(self.cCPPDU.PAYLOAD)
            diff = ac - ex
            print("    LENGTH:     ERROR (EXPECTED: {}, ACTUAL: {}, DIFF: {})".format(ex, ac, diff))

        # Show CRC error
        if crcok:
            print("    CRC:        OK")
        else:
            print("    CRC:        ERROR")
        print()


    def handle_CPPDU(self, cppdu):
        """
        Processes complete CP_PDUs to build a TP_File
        """

        if cppdu.SEQ == "FIRST":
            # Create new TP_File
            self.cTPFile = CCSDS.TP_File(cppdu.PAYLOAD[:-2])

        elif cppdu.SEQ == "CONTINUE":
            # Add data to TP_File
            self.cTPFile.append(cppdu.PAYLOAD[:-2])

        elif cppdu.SEQ == "LAST":
            # Close current TP_File
            lenok = self.cTPFile.finish(cppdu.PAYLOAD[:-2])

            if self.verbose: self.cTPFile.print_info()
            if lenok:
                if self.verbose: print("    LENGTH:     OK\n")
                
                # Handle S_PDU (decryption)
                spdu = CCSDS.S_PDU(self.cTPFile.PAYLOAD, self.keys)

                # Create new xRIT file
                xrit = CCSDS.xRIT(spdu.PLAINTEXT)
                xrit.save(self.outputPath)
                xrit.print_info()

            elif not lenok:
                ex = self.cTPFile.LENGTH
                ac = len(self.cTPFile.PAYLOAD)
                diff = ac - ex

                if self.verbose: print("    LENGTH:     ERROR (EXPECTED: {}, ACTUAL: {}, DIFF: {})".format(ex, ac, diff))
                print("  SKIPPING FILE (DROPPED PACKETS?)")
