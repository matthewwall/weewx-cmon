cmon - Computer Monitor
Copyright 2014 Matthew Wall

cmon is a weewx extension to monitor computer metrics such as cpu usage,
memory use, disk space, and ups status.  It saves data to its own database,
then those data can be displayed in weewx reports.  This extension also
includes a sample skin that illustrates how to use the data.

Installation instructions:

1) run the installer:

wee_extension --install weewx-cmon.tgz

2) restart weewx:

sudo /etc/init.d/weewx stop
sudo /etc/init.d/weewx start

This will result in a skin called cmon with a single web page that illustrates
how to use the monitoring data.  See comments in cmon.py for customization
options.

The skin recognizes the following options:

  metrics - which metrics should be displayed in the report
  periods - which time periods should be displayed in the report

For example, to enable all of the metrics and all of the periods, put this
in weewx.conf then restart weewx:

[StdReport]
    [[cmon]]
        [[[Extras]]]
            metrics = rx, battery, cpu, load, disk, mem, net, temp, ups, upsvoltage, upstime
            periods = day, week, month, year

Not every metric is available on every machine.
