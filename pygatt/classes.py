from __future__ import print_function

from binascii import unhexlify
import logging
import time

from constants import(
    BACKEND, DEFAULT_CONNECT_TIMEOUT_S, LOG_LEVEL, LOG_FORMAT
)
from exceptions import NoResponseError, NotConnectedError
from gatttool_classes import GATTToolBackend


class BluetoothLEDevice(object):
    """
    Interface for a Bluetooth Low Energy device that can use either the Bluegiga
    BLED112 (cross platform) or GATTTOOL (Linux only) as the backend.
    """
    def __init__(self, mac_address, logfile=None, hci_device='hci0',
                 bled112=None):
        """
        Initialize.

        mac_address -- a string containing the mac address of the BLE device in
                       the following format: "XX:XX:XX:XX:XX:XX"
        logfile -- the file in which to write the logs.
        hci_device -- (GATTTOOL only) the hci_device for gattool to use.
        bled112 -- (BLED112 only) the BLED112_backend object to use.
        """
        # Initialize
        self._backend = None
        self._backend_type = None
        self._callbacks = {}  # Holds pairs of 'uuid_string', function_object

        # Set up logging
        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(LOG_LEVEL)
        if logfile is not None:
            handler = logging.FileHandler(logfile)
        else:  # print to stderr
            handler = logging.StreamHandler()
        formatter = logging.Formatter(fmt=LOG_FORMAT)
        handler.setLevel(LOG_LEVEL)
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

        # Select backend, store mac address, optional delete bonds
        if bled112 is not None:
            self._logger.info("pygatt[BLED112]")
            self._backend = bled112
            self._backend_type = BACKEND['BLED112']
            self._mac_address = bytearray(
                [int(b, 16) for b in mac_address.split(":")])
        else:
            self._logger.info("pygatt[GATTTOOL]")
            # TODO: how to pass pexpect logfile
            self._backend = GATTToolBackend(mac_address, hci_device=hci_device,
                                            loglevel=LOG_LEVEL,
                                            loghandler=handler)
            self._backend_type = BACKEND['GATTTOOL']

    def bond(self):
        """
        Securely Bonds to the BLE device.
        """
        self._logger.info("bond")
        if self._backend_type == BACKEND['BLED112']:
            self._backend.bond()
        elif self._backend_type == BACKEND['GATTTOOL']:
            self._backend.bond()
        else:
            raise NotImplementedError("backend", self._backend_type)

    def connect(self, timeout=DEFAULT_CONNECT_TIMEOUT_S):
        """
        Connect to the BLE device.

        timeout -- the length of time to try to establish a connection before
                   returning.

        """
        self._logger.info("connect")
        if self._backend_type == BACKEND['BLED112']:
            ret = self._backend.connect(self._mac_address, timeout=timeout)
            if not ret:
                raise NotConnectedError("Connect failed")
        elif self._backend_type == BACKEND['GATTTOOL']:
            self._backend.connect(timeout=timeout)
        else:
            raise NotImplementedError("backend", self._backend_type)

    def char_read(self, uuid):
        """
        Reads a Characteristic by UUID.

        uuid -- UUID of Characteristic to read as a string.

        Returns a bytearray containing the characteristic value on success.
        """
        self._logger.info("char_read %s", uuid)
        if self._backend_type == BACKEND['BLED112']:
            handle = self._get_handle(uuid)
            if handle is None:
                raise ValueError("invalid UUID")
            ret = self._backend.char_read(handle)
            if ret is None:
                raise Exception("read failed")
            return ret
        elif self._backend_type == BACKEND['GATTTOOL']:
            return self._backend.char_read_uuid(uuid)
        else:
            raise NotImplementedError("backend", self._backend_type)

    def char_write(self, uuid_write, value, wait_for_response=False,
                   num_packets=1, uuid_recv=None, bled112_timeout=5):
        """
        Writes a value to a given characteristic handle.

        uuid -- the UUID of the characteristic to write to.
        value -- the value as a bytearray to write to the characteristic.
        wait_for_response -- wait for notifications/indications after writing.
        num_packets -- (BLED112 only) the number of notification/indication BLE
                       packets to wait for.
        uuid_recv -- (BLED112 only) the UUID for the characteritic that will
                     send the notification/indication packets.
        bled112_timeout -- number of seconds to wait for notifications before
                           timing out.
        """
        self._logger.info("char_write %s", uuid_write)
        # Write to the characteristic
        if self._backend_type == BACKEND['BLED112']:
            if wait_for_response and (num_packets <= 0):
                raise ValueError("num_packets must be greater than 0")
            handle_write = self._get_handle(uuid_write)
            handle_recv = self._get_handle(uuid_recv)
            ret = self._backend.char_write(handle_write, value)
            if not ret:  # write failed
                raise Exception("write failed")
            if wait_for_response:
                # Wait for num_packets notifications on the receive
                #   characteristic
                notifications = self._backend.get_notifications()
                sec_waited = 0
                while (len(notifications[handle_recv]) < num_packets):
                    if (sec_waited >= bled112_timeout):
                        raise NoResponseError("Timed out after %s seconds",
                                              sec_waited)
                    time.sleep(0.25)  # busy wait
                    notifications = self._backend.get_notifications()
                    sec_waited += 0.25
                # Assemble notification values into one bytearray and delete
                #   notification
                value_list = []
                for i in range(0, num_packets):
                    val = notifications[handle_recv][0]
                    value_list += [b for b in val]
                    self._backend.remove_notification(handle_recv, 0)
                # Callback for notifications
                if uuid_recv in self._callbacks:
                    for cb in self._callbacks[uuid_recv]:
                        cb(bytearray(value_list))
        elif self._backend_type == BACKEND['GATTTOOL']:
            handle = self._backend.get_handle(uuid_write)
            self._backend.char_write(handle, value,
                                     wait_for_response=wait_for_response)
        else:
            raise NotImplementedError("backend", self._backend_type)

    def encrypt(self):
        """
        Form an encrypted, but not bonded, connection.
        """
        self._logger.info("encrypt")
        if self._backend_type == BACKEND['BLED112']:
            self._backend.encrypt()
        elif self._backend_type == BACKEND['GATTTOOL']:
            raise NotImplementedError("pygatt[GATTOOL].encrypt")
        else:
            raise NotImplementedError("backend", self._backend_type)

    def get_rssi(self):
        """
        Get the receiver signal strength indicator (RSSI) value from the BLE
        device (BLED112 only).

        Returns the RSSI value in dBm on success.
        Returns None on failure.
        """
        self._logger.info("get_rssi")
        if self._backend_type == BACKEND['BLED112']:
            # The BLED112 has some strange behavior where it will return 25 for
            # the RSSI value sometimes... Try a maximum of 3 times.
            for i in range(0, 3):
                rssi = self._backend.get_rssi()
                if rssi != 25:
                    return rssi
                time.sleep(0.1)
            return Exception("get rssi failed")
        elif self._backend_type == BACKEND['GATTTOOL']:
            raise NotImplementedError("pygatt[GATTOOL].get_rssi")
        else:
            raise NotImplementedError("backend", self._backend_type)

    def run(self):
        """
        Run a background thread to listen for notifications (GATTTOOL only) or
        run the receiver background thread (BLED112 only).
        """
        self._logger.info("run")
        if self._backend_type == BACKEND['BLED112']:
            self._backend.run()
        elif self._backend_type == BACKEND['GATTTOOL']:
            self._backend.run()
        else:
            raise NotImplementedError("backend", self._backend_type)

    def stop(self):
        """
        Stop the backgroud notification handler in preparation for a disconnect
        (GATTTOOL only) or disconnect and stop the receiver thread (BLED112
        only).
        """
        self._logger.info("stop")
        if self._backend_type == BACKEND['BLED112']:
            self._backend.disconnect()
            self._backend.stop()
        elif self._backend_type == BACKEND['GATTTOOL']:
            self._backend.stop()
        else:
            raise NotImplementedError("backend", self._backend_type)

    def subscribe(self, uuid, callback=None, indication=False):
        """
        Enables subscription to a Characteristic with ability to call callback.

        uuid -- UUID as a string of the characteristic to subscribe to.
        callback -- function to be called when a notification/indication is
                    received on this characteristic.
        indication -- use indications (requires application ACK) rather than
                      notifications (does not requrie application ACK).
        """
        self._logger.info("subscribe to %s with callback %s. indicate = %d",
                          uuid, callback.__name__, indication)
        if self._backend_type == BACKEND['BLED112']:
            self._backend.subscribe(self._uuid_bytearray(uuid),
                                    indicate=indication)
            if callback is not None:
                if uuid not in self._callbacks:
                    self._callbacks[uuid] = []
                self._callbacks[uuid].append(callback)
        elif self._backend_type == BACKEND['GATTTOOL']:
            self._backend.subscribe(uuid, callback=callback,
                                    indication=indication)
        else:
            raise NotImplementedError("backend", self._backend_type)

    def _get_handle(self, uuid):
        """
        Get the handle associated with the UUID.

        uuid -- a UUID in string format.
        """
        self._logger.info("_get_handle %s", uuid)
        uuid = self._uuid_bytearray(uuid)
        if self._backend_type == BACKEND['BLED112']:
            return self._backend.get_handle(uuid)
        elif self._backend_type == BACKEND['GATTTOOL']:
            return self._backend.get_handle(uuid)
        else:
            raise NotImplementedError("backend", self._backend_type)

    def _uuid_bytearray(self, uuid):
        """
        Turns a UUID string in the format "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
        to a bytearray.

        uuid -- the UUID to convert.

        Returns a bytearray containing the UUID.
        """
        self._logger.info("_uuid_bytearray %s", uuid)
        return unhexlify(uuid.replace("-", ""))
