##
# Copyright (c) 2005-2015 Apple Inc. All rights reserved.
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

from pycalendar.datetime import DateTime
from pycalendar.period import Period

from twext.python.clsprop import classproperty

from twisted.internet.defer import inlineCallbacks
from twisted.trial.unittest import TestCase

from twistedcaldav.ical import Component, Property

from txdav.caldav.datastore.scheduling.cuaddress import calendarUserFromCalendarUserAddress
from txdav.caldav.datastore.scheduling.freebusy import FreebusyQuery
from txdav.common.datastore.test.util import CommonCommonTests, populateCalendarsFrom

def normalizeiCalendarText(data):
    data = data.replace("\r\n ", "")
    data = [line for line in data.splitlines() if not (line.startswith("UID") or line.startswith("DTSTAMP"))]
    return "\r\n".join(data) + "\r\n"



class BuildFreeBusyResult (TestCase):
    """
    Test txdav.caldav.datastore.scheduling.freebusy.buildFreeBusyResult
    """

    def test_simple(self):

        data = (
            (
                "#1.1 No busy time",
                FreebusyQuery.FBInfo([], [], []),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                None,
                None,
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.2 No busy time with organizer & attendee",
                FreebusyQuery.FBInfo([], [], []),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.3 With single busy time",
                FreebusyQuery.FBInfo([Period.parseText("20080601T120000Z/20080601T130000Z"), ], [], []),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
FREEBUSY;FBTYPE=BUSY:20080601T120000Z/20080601T130000Z
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.4 With multiple busy time",
                FreebusyQuery.FBInfo(
                    [
                        Period.parseText("20080601T120000Z/20080601T130000Z"),
                        Period.parseText("20080601T140000Z/20080601T150000Z"),
                    ],
                    [],
                    [],
                ),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
FREEBUSY;FBTYPE=BUSY:20080601T120000Z/20080601T130000Z,20080601T140000Z/20080601T150000Z
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.5 With multiple busy time, some overlap",
                FreebusyQuery.FBInfo(
                    [
                        Period.parseText("20080601T120000Z/20080601T130000Z"),
                        Period.parseText("20080601T123000Z/20080601T133000Z"),
                        Period.parseText("20080601T140000Z/20080601T150000Z"),
                        Period.parseText("20080601T150000Z/20080601T160000Z"),
                    ],
                    [],
                    [],
                ),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
FREEBUSY;FBTYPE=BUSY:20080601T120000Z/20080601T133000Z,20080601T140000Z/20080601T160000Z
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.6 With all busy time types",
                FreebusyQuery.FBInfo(
                    [
                        Period.parseText("20080601T120000Z/20080601T130000Z"),
                        Period.parseText("20080601T140000Z/20080601T150000Z"),
                    ],
                    [
                        Period.parseText("20080601T140000Z/20080601T150000Z"),
                    ],
                    [
                        Period.parseText("20080601T160000Z/20080601T170000Z"),
                    ],
                ),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                None,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
FREEBUSY;FBTYPE=BUSY:20080601T120000Z/20080601T130000Z,20080601T140000Z/20080601T150000Z
FREEBUSY;FBTYPE=BUSY-TENTATIVE:20080601T140000Z/20080601T150000Z
FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20080601T160000Z/20080601T170000Z
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
            (
                "#1.7 With single busy time and event details",
                FreebusyQuery.FBInfo(
                    [Period.parseText("20080601T120000Z/20080601T130000Z"), ],
                    [],
                    [],
                ),
                Period.parseText("20080601T000000Z/20080602T000000Z"),
                Property("ORGANIZER", "mailto:user01@example.com"),
                Property("ATTENDEE", "mailto:user02@example.com"),
                [
                    tuple(Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:1234-5678
DTSTAMP:20080601T000000Z
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
END:VEVENT
END:VCALENDAR
""").subcomponents())[0],
                ],
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
END:VEVENT
BEGIN:VFREEBUSY
DTSTART:20080601T000000Z
DTEND:20080602T000000Z
ATTENDEE:mailto:user02@example.com
FREEBUSY;FBTYPE=BUSY:20080601T120000Z/20080601T130000Z
ORGANIZER:mailto:user01@example.com
END:VFREEBUSY
END:VCALENDAR
""",
            ),
        )

        for description, fbinfo, timerange, organizer, attendee, event_details, calendar in data:
            freebusy = FreebusyQuery(None, organizer, None, attendee, None, timerange, None, None, event_details=event_details)
            result = freebusy.buildFreeBusyResult(fbinfo)
            self.assertEqual(normalizeiCalendarText(str(result)), calendar.replace("\n", "\r\n"), msg=description)



class GenerateFreeBusyInfo(CommonCommonTests, TestCase):
    """
    Test txdav.caldav.datastore.scheduling.freebusy.FreebusyQuery
    """

    @inlineCallbacks
    def setUp(self):
        yield super(GenerateFreeBusyInfo, self).setUp()
        yield self.buildStoreAndDirectory()
        yield self.populate()

        self.now = DateTime.getNowUTC()
        self.now.setHHMMSS(0, 0, 0)

        self.now_12H = self.now.duplicate()
        self.now_12H.offsetHours(12)

        self.now_13H = self.now.duplicate()
        self.now_13H.offsetHours(13)

        self.now_1D = self.now.duplicate()
        self.now_1D.offsetDay(1)


    @inlineCallbacks
    def populate(self):
        yield populateCalendarsFrom(self.requirements, self.storeUnderTest())
        self.notifierFactory.reset()


    @classproperty(cache=False)
    def requirements(cls): #@NoSelf
        return {
            "user01": {
                "calendar_1": {
                },
                "inbox": {
                },
            },
            "user02": {
                "calendar_1": {
                },
                "inbox": {
                },
            },
            "user03": {
                "calendar_1": {
                },
                "inbox": {
                },
            },
        }


    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        return self._sqlCalendarStore


    @inlineCallbacks
    def _createCalendarObject(self, data, user, name):
        calendar_collection = (yield self.calendarUnderTest(home=user))
        yield calendar_collection.createCalendarObjectWithName("test.ics", Component.fromString(data))
        yield self.commit()


    @inlineCallbacks
    def test_no_events(self):
        """
        Test when the calendar is empty.
        """

        calendar = (yield self.calendarUnderTest(home="user01", name="calendar_1"))
        fbinfo = FreebusyQuery.FBInfo([], [], [])
        matchtotal = 0
        timerange = Period(self.now, self.now_1D)

        freebusy = FreebusyQuery(None, None, None, None, None, timerange, None, None)
        result = (yield freebusy.generateFreeBusyInfo(calendar, fbinfo, matchtotal))
        self.assertEqual(result, 0)
        self.assertEqual(len(fbinfo.busy), 0)
        self.assertEqual(len(fbinfo.tentative), 0)
        self.assertEqual(len(fbinfo.unavailable), 0)


    @inlineCallbacks
    def test_one_event(self):
        """
        Test when the calendar is empty.
        """

        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:1234-5678
DTSTAMP:20080601T000000Z
DTSTART:%s
DTEND:%s
END:VEVENT
END:VCALENDAR
""" % (self.now_12H.getText(), self.now_13H.getText(),)

        yield self._createCalendarObject(data, "user01", "test.ics")
        calendar = (yield self.calendarUnderTest(home="user01", name="calendar_1"))
        fbinfo = FreebusyQuery.FBInfo([], [], [])
        matchtotal = 0
        timerange = Period(self.now, self.now_1D)

        freebusy = FreebusyQuery(None, None, None, None, None, timerange, None, None)
        result = (yield freebusy.generateFreeBusyInfo(calendar, fbinfo, matchtotal))
        self.assertEqual(result, 1)
        self.assertEqual(fbinfo.busy, [Period.parseText("%s/%s" % (self.now_12H.getText(), self.now_13H.getText(),)), ])
        self.assertEqual(len(fbinfo.tentative), 0)
        self.assertEqual(len(fbinfo.unavailable), 0)


    @inlineCallbacks
    def test_one_event_event_details(self):
        """
        Test when the calendar is empty.
        """

        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:1234-5678
DTSTAMP:20080601T000000Z
DTSTART:%s
DTEND:%s
END:VEVENT
END:VCALENDAR
""" % (self.now_12H.getText(), self.now_13H.getText(),)

        yield self._createCalendarObject(data, "user01", "test.ics")
        calendar = (yield self.calendarUnderTest(home="user01", name="calendar_1"))
        fbinfo = FreebusyQuery.FBInfo([], [], [])
        matchtotal = 0
        timerange = Period(self.now, self.now_1D)
        event_details = []

        organizer = recipient = (yield calendarUserFromCalendarUserAddress("mailto:user01@example.com", self.transactionUnderTest()))

        freebusy = FreebusyQuery(organizer, None, recipient, None, None, timerange, None, None, event_details=event_details)
        freebusy.same_calendar_user = True
        result = (yield freebusy.generateFreeBusyInfo(
            calendar,
            fbinfo,
            matchtotal,
        ))
        self.assertEqual(result, 1)
        self.assertEqual(fbinfo.busy, [Period(self.now_12H, self.now_13H), ])
        self.assertEqual(len(fbinfo.tentative), 0)
        self.assertEqual(len(fbinfo.unavailable), 0)
        self.assertEqual(len(event_details), 1)
        self.assertEqual(str(event_details[0]), str(tuple(Component.fromString(data).subcomponents())[0]))
