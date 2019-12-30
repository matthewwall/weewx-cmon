# Copyright 2013-2020 Matthew Wall, Tom Keffer
"""weewx module that records cpu, memory, disk, and network usage.

This file contains both a weewx driver and a weewx service.

Installation

Put this file in the bin/user directory.


Service Configuration

Add the following to weewx.conf:

[ComputerMonitor]
    data_binding = cmon_binding
    max_age = 2592000 # 30 days; None to store indefinitely

[DataBindings]
    [[cmon_binding]]
        database = cmon_sqlite
        manager = weewx.manager.DaySummaryManager
        table_name = archive
        schema = user.cmon.schema

[Databases]
    [[cmon_sqlite]]
        root = %(WEEWX_ROOT)s
        database_name = archive/cmon.sdb
        driver = weedb.sqlite

[Engine]
    [[Services]]
        archive_services = ..., user.cmon.ComputerMonitor


Driver Configuration

Add the following to weewx.conf:

[Station]
    station_type = ComputerMonitor

[ComputerMonitor]
    polling_interval = 30
    driver = user.cmon


Schema

The default schema is defined in this file.  If you prefer to maintain a schema
different than the default, specify the desired schema in the configuration.
For example, this would be a schema that stores only memory and network data,
and uses eth1 instead of the default eth0:

[DataBindings]
    [[cmon_binding]]
        database = cmon_sqlite
        manager = weewx.manager.DaySummaryManager
        table_name = archive
        [[[schema]]]
            dateTime = INTEGER NOT NULL PRIMARY KEY
            usUnits = INTEGER NOT NULL
            interval = INTEGER NOT NULL
            mem_total = INTEGER
            mem_free = INTEGER
            mem_used = INTEGER
            swap_total = INTEGER
            swap_free = INTEGER
            swap_used = INTEGER
            net_eth1_rbytes = INTEGER
            net_eth1_rpackets = INTEGER
            net_eth1_rerrs = INTEGER
            net_eth1_rdrop = INTEGER
            net_eth1_tbytes = INTEGER
            net_eth1_tpackets = INTEGER
            net_eth1_terrs = INTEGER
            net_eth1_tdrop = INTEGER

Another approach to maintaining a custom schema is to define the schema in the
file user/extensions.py as cmonSchema:

cmonSchema = [
    ('dateTime', 'INTEGER NOT NULL PRIMARY KEY'),
    ('usUnits', 'INTEGER NOT NULL'),
    ('interval', 'INTEGER NOT NULL'),
    ('mem_total','INTEGER'),
    ('mem_free','INTEGER'),
    ('mem_used','INTEGER'),
    ('net_eth1_rbytes','INTEGER'),
    ('net_eth1_rpackets','INTEGER'),
    ('net_eth1_rerrs','INTEGER'),
    ('net_eth1_rdrop','INTEGER'),
    ('net_eth1_tbytes','INTEGER'),
    ('net_eth1_tpackets','INTEGER'),
    ('net_eth1_terrs','INTEGER'),
    ('net_eth1_tdrop','INTEGER'),
    ]

then load it using this configuration:

[DataBindings]
    [[cmon_binding]]
        database = cmon_sqlite
        manager = weewx.manager.DaySummaryManager
        table_name = archive
        schema = user.extensions.cmonSchema
"""

# FIXME: make these methods platform-independent instead of linux-specific
# FIXME: deal with MB/GB in memory sizes
# FIXME: save the total counts instead of the deltas
# FIXME: refactor ups and rpi specialties


from __future__ import with_statement
from __future__ import absolute_import
from __future__ import print_function
import os
import platform
import re
import time
from subprocess import Popen, PIPE
from builtins import input

import weewx
import weeutil.weeutil
from weewx.drivers import AbstractDevice
from weewx.engine import StdService

try:
    # Test for new-style weewx logging by trying to import weeutil.logger
    import weeutil.logger
    import logging
    log = logging.getLogger(__name__)

    def logdbg(msg):
        log.debug(msg)

    def loginf(msg):
        log.info(msg)

    def logerr(msg):
        log.error(msg)

except ImportError:
    # Old-style weewx logging
    import syslog

    def logmsg(level, msg):
        syslog.syslog(level, 'cmon: %s:' % msg)

    def logdbg(msg):
        logmsg(syslog.LOG_DEBUG, msg)

    def loginf(msg):
        logmsg(syslog.LOG_INFO, msg)

    def logerr(msg):
        logmsg(syslog.LOG_ERR, msg)

DRIVER_NAME = "ComputerMonitor"
DRIVER_VERSION = "0.20"

if weewx.__version__ < "3":
    raise weewx.UnsupportedFeature("weewx 3 is required, found %s" %
                                   weewx.__version__)

schema = [
    ('dateTime', 'INTEGER NOT NULL PRIMARY KEY'),
    ('usUnits', 'INTEGER NOT NULL'),
    ('interval', 'INTEGER NOT NULL'),
    ('mem_total', 'INTEGER'),
    ('mem_free', 'INTEGER'),
    ('mem_used', 'INTEGER'),
    ('swap_total', 'INTEGER'),
    ('swap_free', 'INTEGER'),
    ('swap_used', 'INTEGER'),
    ('cpu_user', 'INTEGER'),
    ('cpu_nice', 'INTEGER'),
    ('cpu_system', 'INTEGER'),
    ('cpu_idle', 'INTEGER'),
    ('cpu_iowait', 'INTEGER'),
    ('cpu_irq', 'INTEGER'),
    ('cpu_softirq', 'INTEGER'),
    ('load1', 'REAL'),
    ('load5', 'REAL'),
    ('load15', 'REAL'),
    ('proc_active', 'INTEGER'),
    ('proc_total', 'INTEGER'),

# measure cpu temperature (not all platforms support this)
    ('cpu_temp', 'REAL'),  # degree C
    ('cpu_temp1', 'REAL'), # degree C
    ('cpu_temp2', 'REAL'), # degree C
    ('cpu_temp3', 'REAL'), # degree C
    ('cpu_temp4', 'REAL'), # degree C

# measure rpi attributes (not all platforms support this)
#    ('core_temp','REAL'), # degree C
#    ('core_volt', 'REAL'),
#    ('core_sdram_c', 'REAL'),
#    ('core_sdram_i', 'REAL'),
#    ('core_sdram_p', 'REAL'),
#    ('arm_mem', 'REAL'),
#    ('gpu_mem', 'REAL'),

# the default interface on most linux systems is eth0
    ('net_eth0_rbytes', 'INTEGER'),
    ('net_eth0_rpackets', 'INTEGER'),
    ('net_eth0_rerrs', 'INTEGER'),
    ('net_eth0_rdrop', 'INTEGER'),
    ('net_eth0_tbytes', 'INTEGER'),
    ('net_eth0_tpackets', 'INTEGER'),
    ('net_eth0_terrs', 'INTEGER'),
    ('net_eth0_tdrop', 'INTEGER'),

#    ('net_eth1_rbytes', 'INTEGER'),
#    ('net_eth1_rpackets', 'INTEGER'),
#    ('net_eth1_rerrs', 'INTEGER'),
#    ('net_eth1_rdrop', 'INTEGER'),
#    ('net_eth1_tbytes', 'INTEGER'),
#    ('net_eth1_tpackets', 'INTEGER'),
#    ('net_eth1_terrs', 'INTEGER'),
#    ('net_eth1_tdrop', 'INTEGER'),

# some systems have a wireless interface as wlan0
    ('net_wlan0_rbytes', 'INTEGER'),
    ('net_wlan0_rpackets', 'INTEGER'),
    ('net_wlan0_rerrs', 'INTEGER'),
    ('net_wlan0_rdrop', 'INTEGER'),
    ('net_wlan0_tbytes', 'INTEGER'),
    ('net_wlan0_tpackets', 'INTEGER'),
    ('net_wlan0_terrs', 'INTEGER'),
    ('net_wlan0_tdrop', 'INTEGER'),

# if the computer is an openvpn server, track the tunnel traffic
#    ('net_tun0_rbytes', 'INTEGER'),
#    ('net_tun0_rpackets', 'INTEGER'),
#    ('net_tun0_rerrs', 'INTEGER'),
#    ('net_tun0_rdrop', 'INTEGER'),
#    ('net_tun0_tbytes', 'INTEGER'),
#    ('net_tun0_tpackets', 'INTEGER'),
#    ('net_tun0_terrs', 'INTEGER'),
#    ('net_tun0_tdrop', 'INTEGER'),

# disk volumes will vary, but root is always present
    ('disk_root_total', 'INTEGER'),
    ('disk_root_free', 'INTEGER'),
    ('disk_root_used', 'INTEGER'),
# separate partition for home is not uncommon
    ('disk_home_total', 'INTEGER'),
    ('disk_home_free', 'INTEGER'),
    ('disk_home_used', 'INTEGER'),

# measure the ups parameters if we can
    ('ups_temp', 'REAL'),    # degree C
    ('ups_load', 'REAL'),    # percent
    ('ups_charge', 'REAL'),  # percent
    ('ups_voltage', 'REAL'), # volt
    ('ups_time', 'REAL'),    # seconds
    ]

# this exension will scan for all mounted file system.  these are the
# filesystems we ignore.
IGNORED_MOUNTS = [
    '/lib/init/rw',
    '/proc',
    '/sys',
    '/dev',
    '/afs',
    '/mit',
    '/run',
    '/var/lib/nfs']


def get_collector(hardware=[], ignored_mounts=IGNORED_MOUNTS):
    # see what we are running on
    s = platform.system()
    if s == 'Linux':
        return LinuxCollector(hardware, ignored_mounts)
    elif s == 'Darwin':
        return MacOSXCollector(hardware, ignored_mounts)
    elif s == 'BSD':
        return BSDCollector(hardware, ignored_mounts)
    raise Exception('unsupported system %s' % s)


class Collector(object):

    _CPU_KEYS = ['user','nice','system','idle','iowait','irq','softirq']

    # bytes received
    # packets received
    # packets dropped
    # fifo buffer errors
    # packet framing errors
    # compressed packets
    # multicast frames
    _NET_KEYS = [
        'rbytes','rpackets','rerrs','rdrop','rfifo','rframe','rcomp','rmulti',
        'tbytes','tpackets','terrs','tdrop','tfifo','tframe','tcomp','tmulti'
        ]

    def __init__(self, hardware):
        self.hardware = hardware

    def get_data(self, now_ts=None):
        if now_ts is None:
            now_ts = int(time.time() + 0.5)
        record = dict()
        record['dateTime'] = now_ts
        record['usUnits'] = weewx.METRIC
        if 'rpi' in self.hardware:
            record.update(self._get_rpi_info())
        if 'apcups' in self.hardware:
            record.update(self._get_apcups_info())
        return record

    _DIGITS = re.compile('[\d.]+')

    @staticmethod
    def _get_apcups_info():
        record = dict()
        try:
            cmd = '/sbin/apcaccess'
            p = Popen(cmd, shell=True, stdout=PIPE)
            o = p.communicate()[0]
            for line in o.split('\n'):
                if line.startswith('ITEMP'):
                    m = Collector._DIGITS.search(line)
                    if m:
                        record['ups_temp'] = float(m.group())
                elif line.startswith('LOADPCT'):
                    m = Collector._DIGITS.search(line)
                    if m:
                        record['ups_load'] = float(m.group())
                elif line.startswith('BCHARGE'):
                    m = Collector._DIGITS.search(line)
                    if m:
                        record['ups_charge'] = float(m.group())
                elif line.startswith('OUTPUTV') or line.startswith('BATTV'):
                    m = Collector._DIGITS.search(line)
                    if m:
                        record['ups_voltage'] = float(m.group())
                elif line.startswith('TIMELEFT'):
                    m = Collector._DIGITS.search(line)
                    if m:
                        record['ups_time'] = float(m.group())
        except (ValueError, IOError, KeyError) as e:
            logerr('apcups_info failed: %s' % e)
        return record

    _RPI_VCGENCMD = '/opt/vc/bin/vcgencmd'
    _RPI_VOLT = re.compile('([^:]+):\s+volt=([\d.]+)')
    _RPI_MEM = re.compile('([^=]+)=([\d]+)')

    @staticmethod
    def _get_rpi_info():
        # get raspberry pi measurements
        record = dict()
        try:
            cmd = '%s measure_temp' % Collector._RPI_VCGENCMD
            p = Popen(cmd, shell=True, stdout=PIPE)
            o = p.communicate()[0]
            record['core_temp'] = float(o.replace("'C\n", '').partition('=')[2])
            cmd = '%s measure_volts' % Collector._RPI_VCGENCMD
            p = Popen(cmd, shell=True, stdout=PIPE)
            o = p.communicate()[0]
            for line in o.split('\n'):
                m = Collector._RPI_VOLT.search(line)
                if m:
                    record[m.group(1) + '_volt'] = float(m.group(2))
            cmd = '%s measure_volts' % Collector._RPI_VCGENCMD
            p = Popen(cmd, shell=True, stdout=PIPE)
            o = p.communicate()[0]
            for line in o.split('\n'):
                m = Collector._RPI_MEM.search(line)
                if m:
                    record[m.group(1) + '_mem'] = float(m.group(2))
        except (ValueError, IOError, KeyError) as e:
            logerr('rpi_info failed: %s' % e)
        return record


# this should work on any linux running kernel 2.2 or later
class LinuxCollector(Collector):
    def __init__(self, hardware, ignored_mounts):
        super(LinuxCollector, self).__init__(hardware)

        # provide info about the system on which we are running
        loginf('sysinfo: %s' % ' '.join(os.uname()))
        fn = '/proc/cpuinfo'
        try:
            cpuinfo = self._readproc_dict(fn)
            for key in cpuinfo:
                loginf('cpuinfo: %s: %s' % (key, cpuinfo[key]))
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))

        self.ignored_mounts = ignored_mounts
        self.last_cpu = dict()
        self.last_net = dict()

    @staticmethod
    def _readproc_line(filename):
        """read single line proc file, return the string"""
        info = ''
        with open(filename) as fp:
            info = fp.readline().strip()
        return info

    @staticmethod
    def _readproc_lines(filename):
        """read proc file that has 'name value' format for each line"""
        info = dict()
        with open(filename) as fp:
            for line in fp:
                line = line.replace('  ', ' ')
                (label, data) = line.split(' ', 1)
                info[label] = data
        return info

    @staticmethod
    def _readproc_dict(filename):
        """read proc file that has 'name:value' format for each line"""
        info = dict()
        with open(filename) as fp:
            for line in fp:
                if line.find(':') >= 0:
                    (n, v) = line.split(':', 1)
                    info[n.strip()] = v.strip()
        return info

    def get_data(self, now=None):
        record = super(LinuxCollector, self).get_data(now)

        # read memory status
        fn = '/proc/meminfo'
        try:
            meminfo = self._readproc_dict(fn)
            if meminfo:
                record['mem_total'] = int(meminfo['MemTotal'].split()[0]) # kB
                record['mem_free'] = int(meminfo['MemFree'].split()[0]) # kB
                record['mem_used'] = record['mem_total'] - record['mem_free']
                record['swap_total'] = int(meminfo['SwapTotal'].split()[0]) # kB
                record['swap_free'] = int(meminfo['SwapFree'].split()[0]) # kB
                record['swap_used'] = record['swap_total'] - record['swap_free']
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))

        # get cpu usage
        fn = '/proc/stat'
        try:
            cpuinfo = self._readproc_lines(fn)
            if cpuinfo:
                values = cpuinfo['cpu'].split()[0:7]
                for i, k in enumerate(self._CPU_KEYS):
                    if k in self.last_cpu:
                        record['cpu_' + k] = int(values[i]) - self.last_cpu[k]
                    self.last_cpu[k] = int(values[i])
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))

        # get network usage
        fn = '/proc/net/dev'
        try:
            netinfo = self._readproc_dict(fn)
            if netinfo:
                for iface in netinfo:
                    values = netinfo[iface].split()
                    for i, k in enumerate(self._NET_KEYS):
                        if iface not in self.last_net:
                            self.last_net[iface] = {}
                        if k in self.last_net[iface]:
                            x = int(values[i]) - self.last_net[iface][k]
                            if x < 0:
                                maxcnt = 0x100000000 # 32-bit counter
                                if x + maxcnt < 0:
                                    maxcnt = 0x10000000000000000 # 64-bit counter
                                x += maxcnt
                            record['net_' + iface + '_' + k] = x
                        self.last_net[iface][k] = int(values[i])
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))

#        uptimestr = _readproc_line('/proc/uptime')
#        (uptime,idletime) = uptimestr.split()

        # get load and process information
        fn = '/proc/loadavg'
        try:
            loadstr = self._readproc_line(fn)
            if loadstr:
                (load1, load5, load15, nproc) = loadstr.split()[0:4]
                record['load1'] = float(load1)
                record['load5'] = float(load5)
                record['load15'] = float(load15)
                (num_proc, tot_proc) = nproc.split('/')
                record['proc_active'] = int(num_proc)
                record['proc_total'] = int(tot_proc)
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))

        # read cpu temperature
        tdir = '/sys/class/hwmon/hwmon0/device'
        # rpi keeps cpu temperature in a different location
        tfile = '/sys/class/thermal/thermal_zone0/temp'
        if os.path.exists(tdir):
            try:
                for f in os.listdir(tdir):
                    if f.endswith('_input'):
                        s = self._readproc_line(os.path.join(tdir, f))
                        if s and len(s):
                            n = f.replace('_input', '')
                            t_C = int(s) / 1000 # degree C
                            record['cpu_' + n] = t_C
            except Exception as e:
                logdbg("read failed for %s: %s" % (tdir, e))
        elif os.path.exists(tfile):
            try:
                s = self._readproc_line(tfile)
                t_C = int(s) / 1000 # degree C
                record['cpu_temp'] = t_C
            except Exception as e:
                logdbg("read failed for %s: %s" % (tfile, e))

        # get stats on mounted filesystems
        fn = '/proc/mounts'
        disks = []
        try:
            mntlines = self._readproc_lines(fn)
            if mntlines:
                for mnt in mntlines:
                    mntpt = mntlines[mnt].split()[0]
                    ignore = False
                    if mnt.find(':') >= 0:
                        ignore = True
                    for m in self.ignored_mounts:
                        if mntpt.startswith(m):
                            ignore = True
                            break
                    if not ignore:
                        disks.append(mntpt)
        except Exception as e:
            logdbg("read failed for %s: %s" % (fn, e))
        for disk in disks:
            label = disk.replace('/', '_')
            if label == '_':
                label = '_root'
            st = os.statvfs(disk)
            free = int((st.f_bavail * st.f_frsize) / 1024) # kB
            total = int((st.f_blocks * st.f_frsize) / 1024) # kB
            used = int(((st.f_blocks - st.f_bfree) * st.f_frsize) / 1024) # kB
            record['disk' + label + '_free'] = free
            record['disk' + label + '_total'] = total
            record['disk' + label + '_used'] = used

        return record


class MacOSXCollector(Collector):
    def __init__(self, hardware, ignored_mounts):
        super(MacOSXCollector, self).__init__(hardware)

        # provide info about the system on which we are running
        loginf('sysinfo: %s' % ' '.join(os.uname()))
        self.ignored_mounts = ignored_mounts
        self.last_cpu = dict()
        self.last_net = dict()

    def get_data(self, now=None):
        import psutil

        record = super(MacOSXCollector, self).get_data(now)

        # read memory status
        info = psutil.virtual_memory()
        record['mem_total'] = info.total
        record['mem_free'] = info.free
        record['mem_used'] = info.used
        record['mem_available'] = info.available
        record['mem_active'] = info.active
        record['mem_inactive'] = info.inactive
        record['mem_wired'] = info.wired
        info = psutil.swap_memory()
        record['swap_total'] = info.total
        record['swap_free'] = info.free
        record['swap_used'] = info.used
        record['swap_sin'] = info.sin
        record['swap_sout'] = info.sout

        # get cpu usage
        ncpu = psutil.cpu_count()
        record['cpu_count'] = ncpu
        info = psutil.cpu_times()
        for k in list(set(self._CPU_KEYS) & set(dir(info))):
            v = getattr(info, k, None)
            if k in self.last_cpu:
                if v is not None and self.last_cpu[k] is not None:
                    record['cpu_' + k] = v - self.last_cpu[k]
                else:
                    record['cpu_' + k] = None
            self.last_cpu[k] = v

        # get network usage
        info = psutil.net_io_counters(pernic=True)
        for iface in info:
            for k in list(set(self._NET_KEYS) & set(dir(info[iface]))):
                if iface not in self.last_net:
                    self.last_net[iface] = {}
                v = getattr(info[iface], k, None)
                if k in self.last_net[iface]:
                    x = None
                    if v is not None and self.last_net[iface][k] is not None:
                        x = v - self.last_net[iface][k]
                        if x < 0:
                            maxcnt = 0x100000000 # 32-bit counter
                            if x + maxcnt < 0:
                                maxcnt = 0x10000000000000000 # 64-bit counter
                            x += maxcnt
                    record['net_' + iface + '_' + k] = x
                self.last_net[iface][k] = v

        # uptime
        # FIXME: implement uptime/idletime

        # get load and process information
        # FIXME: implement load

        # read cpu temperature
        # FIXME: implement cpu temperature

        # get stats on mounted filesystems
        info = psutil.disk_partitions()
        for d in info:
            label = d.mountpoint.replace('/', '_')
            if label == '_':
                label = '_root'
            du = psutil.disk_usage(d.mountpoint)
            record['disk' + label + '_free'] = du.free / 1024 # kB
            record['disk' + label + '_total'] = du.total / 1024 # kB
            record['disk' + label + '_used'] = du.used / 1024 # kB
            # FIXME: add io counters per disk

        return record


def loader(config_dict, engine):
    return ComputerMonitorDriver(**config_dict['ComputerMonitor'])

class ComputerMonitorDriver(AbstractDevice):
    """Driver for collecting computer health data."""

    def __init__(self, **stn_dict):
        loginf('driver version is %s' % DRIVER_VERSION)
        self.polling_interval = int(stn_dict.get('polling_interval', 10))
        loginf('polling interval is %s' % self.polling_interval)

        self.ignored_mounts = stn_dict.get('ignored_mounts', IGNORED_MOUNTS)
        self.hardware = stn_dict.get('hardware', [None])
        if not isinstance(self.hardware, list):
            self.hardware = [self.hardware]

        self.collector = get_collector(self.hardware, self.ignored_mounts)

    @property
    def hardware_name(self):
        return 'ComputerMonitor'

    def genLoopPackets(self):
        while True:
            p = self.collector.get_data()
            yield p
            time.sleep(self.polling_interval)
            

class ComputerMonitor(StdService):
    """Collect CPU, Memory, Disk, and other computer information."""

    def __init__(self, engine, config_dict):
        super(ComputerMonitor, self).__init__(engine, config_dict)
        loginf("service version is %s" % DRIVER_VERSION)

        d = config_dict.get('ComputerMonitor', {})
        self.max_age = weeutil.weeutil.to_int(d.get('max_age', 2592000))
        self.ignored_mounts = d.get('ignored_mounts', IGNORED_MOUNTS)
        self.hardware = d.get('hardware', [None])
        if not isinstance(self.hardware, list):
            self.hardware = [self.hardware]

        # get the database parameters we need to function
        binding = d.get('data_binding', 'cmon_binding')
        self.dbm = self.engine.db_binder.get_manager(data_binding=binding,
                                                     initialize=True)

        # be sure schema in database matches the schema we have
        dbcol = self.dbm.connection.columnsOf(self.dbm.table_name)
        dbm_dict = weewx.manager.get_manager_dict(
            config_dict['DataBindings'], config_dict['Databases'], binding)
        memcol = [x[0] for x in dbm_dict['schema']]
        if dbcol != memcol:
            raise Exception('cmon schema mismatch: %s != %s' % (dbcol, memcol))

        self.last_ts = None
        self.collector = get_collector(self.hardware, self.ignored_mounts)
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def shutDown(self):
        try:
            self.dbm.close()
        except:
            pass

    def new_archive_record(self, event):
        """save data to database then prune old records as needed"""
        now = int(time.time() + 0.5)
        delta = now - event.record['dateTime']
        if delta > event.record['interval'] * 60:
            logdbg("Skipping record: time difference %s too big" % delta)
            return
        if self.last_ts is not None:
            self.save_data(self.get_data(now, self.last_ts))
        self.last_ts = now
        if self.max_age is not None:
            self.prune_data(now - self.max_age)

    def save_data(self, record):
        """save data to database"""
        self.dbm.addRecord(record)

    def prune_data(self, ts):
        """delete records with dateTime older than ts"""
        sql = "delete from %s where dateTime < %d" % (self.dbm.table_name, ts)
        self.dbm.getSql(sql)
        try:
            # sqlite databases need some help to stay small
            self.dbm.getSql('vacuum')
        except Exception:
            pass

    def get_data(self, now_ts, last_ts):
        record = self.collector.get_data(now_ts)
        # calculate the interval (an integer), and be sure it is non-zero
        record['interval'] = max(1, int((now_ts - last_ts) / 60))
        return record
        


# To test this extension, do the following:
#
# cd /home/weewx
# PYTHONPATH=bin python bin/user/cmon.py
#
if __name__ == "__main__":
    usage = """%prog [options] [--help] [--debug]"""

    def main():
        import optparse
        import weecfg
        syslog.openlog('wee_cmon', syslog.LOG_PID | syslog.LOG_CONS)
        parser = optparse.OptionParser(usage=usage)
        parser.add_option('--config', dest='cfgfn', type=str, metavar="FILE",
                          help="Use configuration file FILE. Default is /etc/weewx/weewx.conf or /home/weewx/weewx.conf")
        parser.add_option('--binding', dest="binding", metavar="BINDING",
                          default='cmon_binding',
                          help="The data binding to use. Default is 'cmon_binding'.")
        parser.add_option('--test-collector', dest='tc', action='store_true',
                          help='test the data collector')
        parser.add_option('--test-driver', dest='td', action='store_true',
                          help='test the driver')
        parser.add_option('--test-service', dest='ts', action='store_true',
                          help='test the service')
        parser.add_option('--update', dest='update', action='store_true',
                          help='update schema to v3')
        (options, args) = parser.parse_args()

        if options.update:
            _, config_dict = weecfg.read_config(options.cfgfn, args)
            update_to_v3(config_dict, options.binding)
        elif options.tc:
            test_collector()
        elif options.td:
            test_driver()
        elif options.ts:
            test_service()

    def update_to_v3(config_dict, db_binding):
        import weedb
        import sqlite3
        from weewx.manager import Manager

        # update an old schema to v3-compatible.  this means adding the
        # interval column and populating it.
        mgr_dict = weewx.manager.get_manager_dict(config_dict['DataBindings'],
                                                  config_dict['Databases'],
                                                  db_binding)

        # see if update is actually required
        schema_name = mgr_dict.get('schema')
        if schema_name is None:
            s = schema
        elif isinstance(schema_name, str):
            s = weeutil.weeutil._get_object(schema_name)
        else:
            s = mgr_dict['schema']
        if 'interval' in s:
            print("Column 'interval' already exists, update not necessary")
            return

        # make a copy of the database dict
        new_db_dict = dict(mgr_dict['database_dict'])
        # modify the database name
        new_db_dict['database_name'] = mgr_dict['database_dict']['database_name'] + '_new'
        # see if the new db exists.  if so, see if we can delete it
        try:
            weedb.create(new_db_dict)
        except weedb.DatabaseExists:
            ans = None
            while ans not in ['y', 'n']:
                ans = input("New database '%s' already exists. Delete it (y/n)? " % (new_db_dict['database_name'],))
                if ans == 'y':
                    weedb.drop(new_db_dict)
                elif ans == 'n':
                    print("update aborted")
                    return
        except sqlite3.OperationalError:
            pass

        # see if we really should do this
        ans = None
        while ans not in ['y', 'n']:
            ans = input("Create new database %s (y/n)? " % new_db_dict['database_name'])
            if ans == 'y':
                break
            elif ans == 'n':
                print("update aborted")
                return

        # copy the data over
        cnt = 0
        with Manager.open(mgr_dict['database_dict']) as old_mgr:
            with Manager.open_with_create(new_db_dict, schema=s) as new_mgr:
                last_ts = None
                for r in old_mgr.genBatchRecords():
                    cnt += 1
                    print("record %d\r" % cnt, end=' ')
                    ival = r['dateTime'] - last_ts if last_ts else 0
                    r['interval'] = ival
                    new_mgr.addRecord(r)
                    last_ts = r['dateTime']
        print("copied %d records\n" % cnt)

    def test_collector():
        c = get_collector()
        while True:
            print(c.get_data())
            time.sleep(5)

    def test_driver():
        driver = ComputerMonitorDriver()
        for p in driver.genLoopPackets():
            print(p)

    def test_service():
        from weewx.engine import StdEngine
        config = {
            'Station': {
                'station_type': 'Simulator',
                'altitude': [0, 'foot'],
                'latitude': 0,
                'longitude': 0},
            'Simulator': {
                'driver': 'weewx.drivers.simulator',
                'mode': 'simulator'},
            'ComputerMonitor': {
                'binding': 'cmon_binding'},
            'DataBindings': {
                'cmon_binding': {
                    'database': 'cmon_sqlite',
                    'manager': 'weewx.manager.DaySummaryManager',
                    'table_name': 'archive',
                    'schema': 'user.cmon.schema'}},
            'Databases': {
                'cmon_sqlite': {
                    'root': '%(WEEWX_ROOT)s',
                    'database_name': '/tmp/cmon.sdb',
                    'driver': 'weedb.sqlite'}},
            'Engine': {
                'Services': {
                    'archive_services': 'user.cmon.ComputerMonitor'}}}
        engine = StdEngine(config)
        svc = ComputerMonitor(engine, config)
        now_ts = int(time.time() + 0.5)
        last_ts = None
        for _ in range(4):
            if last_ts is not None:
                record = svc.get_data(now_ts, last_ts)
                print(record)
            last_ts = now_ts
            time.sleep(5)
        os.remove('/tmp/cmon.sdb')

    main()
