##
# Copyright (c) 2012-2016 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##
from __future__ import print_function

from StringIO import StringIO
from caldavclientlibrary.client.clientsession import CalDAVSession
from caldavclientlibrary.protocol.url import URL
from caldavclientlibrary.protocol.webdav.definitions import davxml
from calendarserver.tools import tables
from contrib.performance.sqlusage.requests.invite import InviteTest
from contrib.performance.sqlusage.requests.multiget import MultigetTest
from contrib.performance.sqlusage.requests.propfind import PropfindTest
from contrib.performance.sqlusage.requests.propfind_invite import PropfindInviteTest
from contrib.performance.sqlusage.requests.put import PutTest
from contrib.performance.sqlusage.requests.query import QueryTest
from contrib.performance.sqlusage.requests.sync import SyncTest
from pycalendar.datetime import DateTime
from txweb2.dav.util import joinURL
import getopt
import itertools
import sys
from caldavclientlibrary.client.principal import principalCache

"""
This tool is designed to analyze how SQL is being used for various HTTP requests.
It will execute a series of HTTP requests against a test server configuration and
count the total number of SQL statements per request, the total number of rows
returned per request and the total SQL execution time per request. Each series
will be repeated against a varying calendar size so the variation in SQL use
with calendar size can be plotted.
"""

EVENT_COUNTS = (0, 1, 5, 10, 50, 100, 500, 1000,)
SHAREE_COUNTS = (0, 1, 5, 10, 50, 100,)

ICAL = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VTIMEZONE
LAST-MODIFIED:20040110T032845Z
TZID:US/Eastern
BEGIN:DAYLIGHT
DTSTART:20000404T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
TZNAME:EDT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20001026T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZNAME:EST
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:%d0101T100000
DURATION:PT1H
SUMMARY:event 1
UID:%d-ics
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

class SQLUsageSession(CalDAVSession):

    def __init__(self, server, port=None, ssl=False, afunix=None, user="", pswd="", principal=None, root=None, calendar="calendar", logging=False):

        super(SQLUsageSession, self).__init__(server, port, ssl, afunix, user, pswd, principal, root, logging)
        self.homeHref = "/calendars/users/%s/" % (self.user,)
        self.calendarHref = "/calendars/users/%s/%s/" % (self.user, calendar,)
        self.inboxHref = "/calendars/users/%s/inbox/" % (self.user,)
        self.notificationHref = "/calendars/users/%s/notification/" % (self.user,)



class EventSQLUsage(object):

    def __init__(self, server, port, users, pswds, logFilePath, compact):
        self.server = server
        self.port = port
        self.users = users
        self.pswds = pswds
        self.logFilePath = logFilePath
        self.compact = compact
        self.requestLabels = []
        self.results = {}
        self.currentCount = 0


    def runLoop(self, event_counts):

        # Make the sessions
        sessions = [
            SQLUsageSession(self.server, self.port, user=user, pswd=pswd, root="/")
            for user, pswd in itertools.izip(self.users, self.pswds)
        ]

        # Set of requests to execute
        requests = [
            MultigetTest("mget-1" if self.compact else "multiget-1", sessions, self.logFilePath, "event", 1),
            MultigetTest("mget-50" if self.compact else "multiget-50", sessions, self.logFilePath, "event", 50),
            PropfindTest("prop-cal" if self.compact else "propfind-cal", sessions, self.logFilePath, "event", 1),
            SyncTest("s-full" if self.compact else "sync-full", sessions, self.logFilePath, "event", True, 0),
            SyncTest("s-1" if self.compact else "sync-1", sessions, self.logFilePath, "event", False, 1),
            QueryTest("q-1" if self.compact else "query-1", sessions, self.logFilePath, "event", 1),
            QueryTest("q-10" if self.compact else "query-10", sessions, self.logFilePath, "event", 10),
            PutTest("put", sessions, self.logFilePath, "event"),
            InviteTest("invite-1", sessions, self.logFilePath, "event", 1),
            InviteTest("invite-5", sessions, self.logFilePath, "event", 5),
        ]
        self.requestLabels = [request.label for request in requests]

        def _warmUp():
            # Warm-up server by doing calendar home and child collection propfinds.
            # Do this twice because the very first time might provision DB objects and
            # blow any DB cache - the second time will warm the DB cache.
            props = (davxml.resourcetype,)
            for _ignore in range(2):
                for session in sessions:
                    session.getPropertiesOnHierarchy(URL(path=session.homeHref), props)
                    session.getPropertiesOnHierarchy(URL(path=session.calendarHref), props)
                    session.getPropertiesOnHierarchy(URL(path=session.inboxHref), props)
                    session.getPropertiesOnHierarchy(URL(path=session.notificationHref), props)

        # Now loop over sets of events
        for count in event_counts:
            print("Testing count = %d" % (count,))
            self.ensureEvents(sessions[0], sessions[0].calendarHref, count)
            result = {}
            for request in requests:
                print("  Test = %s" % (request.label,))
                _warmUp()
                result[request.label] = request.execute(count)
            self.results[count] = result


    def report(self):

        self._printReport("SQL Statement Count", "count", "%d")
        self._printReport("SQL Rows Returned", "rows", "%d")
        self._printReport("SQL Time", "timing", "%.1f")


    def _printReport(self, title, attr, colFormat):
        table = tables.Table()

        print(title)
        headers = ["Events"] + self.requestLabels
        table.addHeader(headers)
        formats = [tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY)] + \
            [tables.Table.ColumnFormat(colFormat, tables.Table.ColumnFormat.RIGHT_JUSTIFY)] * len(self.requestLabels)
        table.setDefaultColumnFormats(formats)
        for k in sorted(self.results.keys()):
            row = [k] + [getattr(self.results[k][item], attr) for item in self.requestLabels]
            table.addRow(row)
        os = StringIO()
        table.printTable(os=os)
        print(os.getvalue())
        print("")


    def ensureEvents(self, session, calendarhref, n):
        """
        Make sure the required number of events are present in the calendar.

        @param n: number of events
        @type n: C{int}
        """
        now = DateTime.getNowUTC()
        for i in range(n - self.currentCount):
            index = self.currentCount + i + 1
            href = joinURL(calendarhref, "%d.ics" % (index,))
            session.writeData(URL(path=href), ICAL % (now.getYear() + 1, index,), "text/calendar")

        self.currentCount = n



class SharerSQLUsage(object):

    def __init__(self, server, port, users, pswds, logFilePath, compact):
        self.server = server
        self.port = port
        self.users = users
        self.pswds = pswds
        self.logFilePath = logFilePath
        self.compact = compact
        self.requestLabels = []
        self.results = {}
        self.currentCount = 0


    def runLoop(self, sharee_counts):

        # Make the sessions
        sessions = [
            SQLUsageSession(self.server, self.port, user=user, pswd=pswd, root="/", calendar="shared")
            for user, pswd in itertools.izip(self.users, self.pswds)
        ]
        sessions = sessions[0:1]

        # Create the calendar first
        sessions[0].makeCalendar(URL(path=sessions[0].calendarHref))

        # Set of requests to execute
        requests = [
            MultigetTest("mget-1" if self.compact else "multiget-1", sessions, self.logFilePath, "share", 1),
            MultigetTest("mget-50" if self.compact else "multiget-50", sessions, self.logFilePath, "share", 50),
            PropfindInviteTest("propfind", sessions, self.logFilePath, "share", 1),
            SyncTest("s-full" if self.compact else "sync-full", sessions, self.logFilePath, "share", True, 0),
            SyncTest("s-1" if self.compact else "sync-1", sessions, self.logFilePath, "share", False, 1),
            QueryTest("q-1" if self.compact else "query-1", sessions, self.logFilePath, "share", 1),
            QueryTest("q-10" if self.compact else "query-10", sessions, self.logFilePath, "share", 10),
            PutTest("put", sessions, self.logFilePath, "share"),
        ]
        self.requestLabels = [request.label for request in requests]

        # Warm-up server by doing shared calendar propfinds
        props = (davxml.resourcetype,)
        for session in sessions:
            session.getPropertiesOnHierarchy(URL(path=session.calendarHref), props)

        # Now loop over sets of events
        for count in sharee_counts:
            print("Testing count = %d" % (count,))
            self.ensureSharees(sessions[0], sessions[0].calendarHref, count)
            result = {}
            for request in requests:
                print("  Test = %s" % (request.label,))
                result[request.label] = request.execute(count)
            self.results[count] = result


    def report(self):

        self._printReport("SQL Statement Count", "count", "%d")
        self._printReport("SQL Rows Returned", "rows", "%d")
        self._printReport("SQL Time", "timing", "%.1f")


    def _printReport(self, title, attr, colFormat):
        table = tables.Table()

        print(title)
        headers = ["Sharees"] + self.requestLabels
        table.addHeader(headers)
        formats = [tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY)] + \
            [tables.Table.ColumnFormat(colFormat, tables.Table.ColumnFormat.RIGHT_JUSTIFY)] * len(self.requestLabels)
        table.setDefaultColumnFormats(formats)
        for k in sorted(self.results.keys()):
            row = [k] + [getattr(self.results[k][item], attr) for item in self.requestLabels]
            table.addRow(row)
        os = StringIO()
        table.printTable(os=os)
        print(os.getvalue())
        print("")


    def ensureSharees(self, session, calendarhref, n):
        """
        Make sure the required number of sharees are present in the calendar.

        @param n: number of sharees
        @type n: C{int}
        """

        users = []
        uids = []
        for i in range(n - self.currentCount):
            index = self.currentCount + i + 2
            users.append("user%02d" % (index,))
            uids.append("urn:x-uid:10000000-0000-0000-0000-000000000%03d" % (index,))
        session.addInvitees(URL(path=calendarhref), uids, True)

        # Now accept each one
        for user in users:
            acceptor = SQLUsageSession(self.server, self.port, user=user, pswd=user, root="/", calendar="shared")
            notifications = acceptor.getNotifications(URL(path=acceptor.notificationHref))
            principal = principalCache.getPrincipal(acceptor, acceptor.principalPath)
            acceptor.processNotification(principal, notifications[0], True)

        self.currentCount = n



def usage(error_msg=None):
    if error_msg:
        print(error_msg)

    print("""Usage: sqlusage.py [options] [FILE]
Options:
    -h             Print this help and exit
    --server       Server hostname
    --port         Server port
    --user         User name
    --pswd         Password
    --event        Do event scaling
    --share        Do sharee sclaing
    --event-counts       Comma-separated list of event counts to test
    --sharee-counts      Comma-separated list of sharee counts to test
    --compact      Make printed tables as thin as possible

Arguments:
    FILE           File name for sqlstats.log to analyze.

Description:
This utility will analyze the output of s pg_stat_statement table.
""")

    if error_msg:
        raise ValueError(error_msg)
    else:
        sys.exit(0)

if __name__ == '__main__':

    server = "localhost"
    port = 8008
    users = ("user01", "user02",)
    pswds = ("user01", "user02",)
    file = "sqlstats.logs"
    event_counts = EVENT_COUNTS
    sharee_counts = SHAREE_COUNTS
    compact = False

    do_all = True
    do_event = False
    do_share = False

    options, args = getopt.getopt(
        sys.argv[1:],
        "h",
        [
            "server=", "port=",
            "user=", "pswd=",
            "compact",
            "event", "share",
            "event-counts=", "sharee-counts=",
        ]
    )

    for option, value in options:
        if option == "-h":
            usage()
        elif option == "--server":
            server = value
        elif option == "--port":
            port = int(value)
        elif option == "--user":
            users = value.split(",")
        elif option == "--pswd":
            pswds = value.split(",")
        elif option == "--compact":
            compact = True
        elif option == "--event":
            do_all = False
            do_event = True
        elif option == "--share":
            do_all = False
            do_share = True
        elif option == "--event-counts":
            event_counts = [int(i) for i in value.split(",")]
        elif option == "--sharee-counts":
            sharee_counts = [int(i) for i in value.split(",")]
        else:
            usage("Unrecognized option: %s" % (option,))

    # Process arguments
    if len(args) == 1:
        file = args[0]
    elif len(args) != 0:
        usage("Must zero or one file arguments")

    if do_all or do_event:
        sql = EventSQLUsage(server, port, users, pswds, file, compact)
        sql.runLoop(event_counts)
        sql.report()

    if do_all or do_share:
        sql = SharerSQLUsage(server, port, users, pswds, file, compact)
        sql.runLoop(sharee_counts)
        sql.report()
