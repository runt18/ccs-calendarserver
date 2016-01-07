##
# Copyright (c) 2010-2016 Apple Inc. All rights reserved.
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

"""
Tests for L{txdav.common.datastore.upgrade.migrate}.
"""

from twext.enterprise.adbapi2 import Pickle
from twext.enterprise.dal.syntax import Delete
from twext.python.filepath import CachingFilePath
from txweb2.http_headers import MimeType

from twisted.internet.defer import inlineCallbacks, Deferred, returnValue
from twisted.internet.protocol import Protocol
from twisted.protocols.amp import AMP, Command, String
from twisted.python.modules import getModule
from twisted.python.reflect import qual, namedAny
from twisted.trial.unittest import TestCase

from twistedcaldav import customxml, caldavxml
from twistedcaldav.config import config
from twistedcaldav.ical import Component

from txdav.base.propertystore.base import PropertyName
from txdav.caldav.datastore.test.common import CommonTests
from txdav.carddav.datastore.test.common import CommonTests as ABCommonTests
from txdav.common.datastore.file import CommonDataStore
from txdav.common.datastore.sql_tables import schema
from txdav.common.datastore.test.util import SQLStoreBuilder
from txdav.common.datastore.test.util import (
    populateCalendarsFrom, StubNotifierFactory, resetCalendarMD5s,
    populateAddressBooksFrom, resetAddressBookMD5s, deriveValue,
    withSpecialValue, CommonCommonTests
)
from txdav.common.datastore.upgrade.migrate import UpgradeToDatabaseStep, \
    StoreSpawnerService, swapAMP
from txdav.xml import element

import copy



class CreateStore(Command):
    """
    Create a store in a subprocess.
    """
    arguments = [('delegateTo', String())]



class PickleConfig(Command):
    """
    Unpickle some configuration in a subprocess.
    """
    arguments = [('delegateTo', String()),
                 ('config', Pickle())]



class StoreCreator(AMP):
    """
    Helper protocol.
    """

    @CreateStore.responder
    def createStore(self, delegateTo):
        """
        Create a store and pass it to the named delegate class.
        """
        swapAMP(self, namedAny(delegateTo)(SQLStoreBuilder.childStore()))
        return {}


    @PickleConfig.responder
    def pickleConfig(self, config, delegateTo):
        # from twistedcaldav.config import config as globalConfig
        # globalConfig._data = config._data
        swapAMP(self, namedAny(delegateTo)(config))
        return {}



class StubSpawner(StoreSpawnerService):
    """
    Stub spawner service which populates the store forcibly.
    """

    def __init__(self, config=None):
        super(StubSpawner, self).__init__()
        self.config = config


    @inlineCallbacks
    def spawnWithStore(self, here, there):
        """
        'here' and 'there' are the helper protocols 'there' will expect to be
        created with an instance of a store.
        """
        master = yield self.spawn(AMP(), StoreCreator)
        yield master.callRemote(CreateStore, delegateTo=qual(there))
        returnValue(swapAMP(master, here))


    @inlineCallbacks
    def spawnWithConfig(self, config, here, there):
        """
        Similar to spawnWithStore except the child process gets a configuration
        object instead.
        """
        master = yield self.spawn(AMP(), StoreCreator)
        subcfg = copy.deepcopy(self.config)
        del subcfg._postUpdateHooks[:]
        yield master.callRemote(PickleConfig, config=subcfg,
                                delegateTo=qual(there))
        returnValue(swapAMP(master, here))



class HomeMigrationTests(CommonCommonTests, TestCase):
    """
    Tests for L{UpgradeToDatabaseStep}.
    """

    av1 = Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//calendarserver.org//Zonal//EN
BEGIN:VAVAILABILITY
ORGANIZER:mailto:user01@example.com
UID:1@example.com
DTSTAMP:20061005T133225Z
DTEND:20140101T000000Z
BEGIN:AVAILABLE
UID:1-1@example.com
DTSTAMP:20061005T133225Z
SUMMARY:Monday to Friday from 9:00 to 17:00
DTSTART:20130101T090000Z
DTEND:20130101T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
""")


    @inlineCallbacks
    def setUp(self):
        """
        Set up two stores to migrate between.
        """

        yield super(HomeMigrationTests, self).setUp()
        yield self.buildStoreAndDirectory(
            extraUids=(
                u"home1",
                u"home2",
                u"home3",
                u"home_defaults",
                u"home_no_splits",
                u"home_splits",
                u"home_splits_shared",
            )
        )
        self.sqlStore = self.store

        # Add some files to the file store.

        self.filesPath = CachingFilePath(self.mktemp())
        self.filesPath.createDirectory()
        fileStore = self.fileStore = CommonDataStore(
            self.filesPath, {"push": StubNotifierFactory()}, self.directory, True, True
        )
        self.upgrader = UpgradeToDatabaseStep(self.fileStore, self.sqlStore)

        requirements = CommonTests.requirements
        extras = deriveValue(self, "extraRequirements", lambda t: {})
        requirements = self.mergeRequirements(requirements, extras)

        yield populateCalendarsFrom(requirements, fileStore)
        md5s = CommonTests.md5s
        yield resetCalendarMD5s(md5s, fileStore)
        self.filesPath.child("calendars").child(
            "__uids__").child("ho").child("me").child("home1").child(
            ".some-extra-data").setContent("some extra data")

        requirements = ABCommonTests.requirements
        yield populateAddressBooksFrom(requirements, fileStore)
        md5s = ABCommonTests.md5s
        yield resetAddressBookMD5s(md5s, fileStore)
        self.filesPath.child("addressbooks").child(
            "__uids__").child("ho").child("me").child("home1").child(
            ".some-extra-data").setContent("some extra data")

        # Add some properties we want to check get migrated over
        txn = self.fileStore.newTransaction()
        home = yield txn.calendarHomeWithUID("home_defaults")

        cal = yield home.calendarWithName("calendar_1")
        props = cal.properties()
        props[PropertyName.fromElement(caldavxml.SupportedCalendarComponentSet)] = caldavxml.SupportedCalendarComponentSet(
            caldavxml.CalendarComponent(name="VEVENT"),
            caldavxml.CalendarComponent(name="VTODO"),
        )
        props[PropertyName.fromElement(element.ResourceType)] = element.ResourceType(
            element.Collection(),
            caldavxml.Calendar(),
        )
        props[PropertyName.fromElement(customxml.GETCTag)] = customxml.GETCTag.fromString("foobar")

        inbox = yield home.calendarWithName("inbox")
        props = inbox.properties()
        props[PropertyName.fromElement(customxml.CalendarAvailability)] = customxml.CalendarAvailability.fromString(str(self.av1))
        props[PropertyName.fromElement(caldavxml.ScheduleDefaultCalendarURL)] = caldavxml.ScheduleDefaultCalendarURL(
            element.HRef.fromString("/calendars/__uids__/home_defaults/calendar_1"),
        )

        yield txn.commit()


    def mergeRequirements(self, a, b):
        """
        Merge two requirements dictionaries together, modifying C{a} and
        returning it.

        @param a: Some requirements, in the format of
            L{CommonTests.requirements}.
        @type a: C{dict}

        @param b: Some additional requirements, to be merged into C{a}.
        @type b: C{dict}

        @return: C{a}
        @rtype: C{dict}
        """
        for homeUID in b:
            homereq = a.setdefault(homeUID, {})
            homeExtras = b[homeUID]
            for calendarUID in homeExtras:
                calreq = homereq.setdefault(calendarUID, {})
                calendarExtras = homeExtras[calendarUID]
                calreq.update(calendarExtras)
        return a


    @withSpecialValue(
        "extraRequirements",
        {
            "home1": {
                "calendar_1": {
                    "bogus.ics": (
                        getModule("twistedcaldav").filePath.sibling("zoneinfo")
                        .child("EST.ics").getContent(),
                        CommonTests.metadata1
                    )
                }
            }
        }
    )
    @inlineCallbacks
    def test_unknownTypeNotMigrated(self):
        """
        The only types of calendar objects that should get migrated are VEVENTs
        and VTODOs.  Other component types, such as free-standing VTIMEZONEs,
        don't have a UID and can't be stored properly in the database, so they
        should not be migrated.
        """
        yield self.upgrader.stepWithResult(None)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)
        self.assertIdentical(
            None,
            (yield (yield (yield (
                yield txn.calendarHomeWithUID("home1")
            ).calendarWithName("calendar_1"))
            ).calendarObjectWithName("bogus.ics"))
        )


    @inlineCallbacks
    def test_upgradeCalendarHomes(self):
        """
        L{UpgradeToDatabaseService.startService} will do the upgrade, then
        start its dependent service by adding it to its service hierarchy.
        """

        # Create a fake directory in the same place as a home, but with a non-existent uid
        fake_dir = self.filesPath.child("calendars").child("__uids__").child("ho").child("me").child("foobar")
        fake_dir.makedirs()

        # Create a fake file in the same place as a home,with a name that matches the hash uid prefix
        fake_file = self.filesPath.child("calendars").child("__uids__").child("ho").child("me").child("home_file")
        fake_file.setContent("")

        yield self.upgrader.stepWithResult(None)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)
        for uid in CommonTests.requirements:
            if CommonTests.requirements[uid] is not None:
                self.assertNotIdentical(
                    None, (yield txn.calendarHomeWithUID(uid))
                )
        # Successfully migrated calendar homes are deleted
        self.assertFalse(self.filesPath.child("calendars").child(
            "__uids__").child("ho").child("me").child("home1").exists())

        # Want metadata preserved
        home = (yield txn.calendarHomeWithUID("home1"))
        calendar = (yield home.calendarWithName("calendar_1"))
        for name, metadata, md5 in (
            ("1.ics", CommonTests.metadata1, CommonTests.md5Values[0]),
            ("2.ics", CommonTests.metadata2, CommonTests.md5Values[1]),
            ("3.ics", CommonTests.metadata3, CommonTests.md5Values[2]),
        ):
            object = (yield calendar.calendarObjectWithName(name))
            self.assertEquals(object.getMetadata(), metadata)
            self.assertEquals(object.md5(), md5)


    @withSpecialValue(
        "extraRequirements",
        {
            "nonexistent": {
                "calendar_1": {
                }
            }
        }
    )
    @inlineCallbacks
    def test_upgradeCalendarHomesMissingDirectoryRecord(self):
        """
        Test an upgrade where a directory record is missing for a home;
        the original home directory will remain on disk.
        """
        yield self.upgrader.stepWithResult(None)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)
        for uid in CommonTests.requirements:
            if CommonTests.requirements[uid] is not None:
                self.assertNotIdentical(
                    None, (yield txn.calendarHomeWithUID(uid))
                )
        self.assertIdentical(None, (yield txn.calendarHomeWithUID(u"nonexistent")))
        # Skipped calendar homes are not deleted
        self.assertTrue(self.filesPath.child("calendars").child(
            "__uids__").child("no").child("ne").child("nonexistent").exists())


    @inlineCallbacks
    def test_upgradeExistingHome(self):
        """
        L{UpgradeToDatabaseService.startService} will skip migrating existing
        homes.
        """
        startTxn = self.sqlStore.newTransaction("populate empty sample")
        yield startTxn.calendarHomeWithUID("home1", create=True)
        yield startTxn.commit()
        yield self.upgrader.stepWithResult(None)
        vrfyTxn = self.sqlStore.newTransaction("verify sample still empty")
        self.addCleanup(vrfyTxn.commit)
        home = yield vrfyTxn.calendarHomeWithUID("home1")
        # The default calendar is still there.
        self.assertNotIdentical(None, (yield home.calendarWithName("calendar")))
        # The migrated calendar isn't.
        self.assertIdentical(None, (yield home.calendarWithName("calendar_1")))


    @inlineCallbacks
    def test_upgradeAttachments(self):
        """
        L{UpgradeToDatabaseService.startService} upgrades calendar attachments
        as well.
        """

        # Need to tweak config and settings to setup dropbox to work
        self.patch(config, "EnableDropBox", True)
        self.patch(config, "EnableManagedAttachments", False)
        self.sqlStore.enableManagedAttachments = False

        txn = self.sqlStore.newTransaction()
        cs = schema.CALENDARSERVER
        yield Delete(
            From=cs,
            Where=cs.NAME == "MANAGED-ATTACHMENTS"
        ).on(txn)
        yield txn.commit()

        txn = self.fileStore.newTransaction()
        committed = []
        def maybeCommit():
            if not committed:
                committed.append(True)
                return txn.commit()
        self.addCleanup(maybeCommit)

        @inlineCallbacks
        def getSampleObj():
            home = (yield txn.calendarHomeWithUID("home1"))
            calendar = (yield home.calendarWithName("calendar_1"))
            object = (yield calendar.calendarObjectWithName("1.ics"))
            returnValue(object)

        inObject = yield getSampleObj()
        someAttachmentName = "some-attachment"
        someAttachmentType = MimeType.fromString("application/x-custom-type")
        attachment = yield inObject.createAttachmentWithName(
            someAttachmentName,
        )
        transport = attachment.store(someAttachmentType)
        someAttachmentData = "Here is some data for your attachment, enjoy."
        transport.write(someAttachmentData)
        yield transport.loseConnection()
        yield maybeCommit()
        yield self.upgrader.stepWithResult(None)
        committed = []
        txn = self.sqlStore.newTransaction()
        outObject = yield getSampleObj()
        outAttachment = yield outObject.attachmentWithName(someAttachmentName)
        allDone = Deferred()
        class SimpleProto(Protocol):
            data = ''
            def dataReceived(self, data):
                self.data += data
            def connectionLost(self, reason):
                allDone.callback(self.data)
        self.assertEquals(outAttachment.contentType(), someAttachmentType)
        outAttachment.retrieve(SimpleProto())
        allData = yield allDone
        self.assertEquals(allData, someAttachmentData)


    @inlineCallbacks
    def test_upgradeAddressBookHomes(self):
        """
        L{UpgradeToDatabaseService.startService} will do the upgrade, then
        start its dependent service by adding it to its service hierarchy.
        """
        yield self.upgrader.stepWithResult(None)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)
        for uid in ABCommonTests.requirements:
            if ABCommonTests.requirements[uid] is not None:
                self.assertNotIdentical(
                    None, (yield txn.addressbookHomeWithUID(uid))
                )
        # Successfully migrated addressbook homes are deleted
        self.assertFalse(self.filesPath.child("addressbooks").child(
            "__uids__").child("ho").child("me").child("home1").exists())

        # Want metadata preserved
        home = (yield txn.addressbookHomeWithUID("home1"))
        adbk = (yield home.addressbookWithName("addressbook"))
        for name, md5 in (
            ("1.vcf", ABCommonTests.md5Values[0]),
            ("2.vcf", ABCommonTests.md5Values[1]),
            ("3.vcf", ABCommonTests.md5Values[2]),
        ):
            object = (yield adbk.addressbookObjectWithName(name))
            self.assertEquals(object.md5(), md5)


    @inlineCallbacks
    def test_upgradeProperties(self):
        """
        L{UpgradeToDatabaseService.startService} will do the upgrade, then
        start its dependent service by adding it to its service hierarchy.
        """
        yield self.upgrader.stepWithResult(None)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)

        # Want metadata preserved
        home = (yield txn.calendarHomeWithUID("home_defaults"))
        cal = (yield home.calendarWithName("calendar_1"))
        inbox = (yield home.calendarWithName("inbox"))

        # Supported components
        self.assertEqual(cal.getSupportedComponents(), "VEVENT")
        self.assertTrue(cal.properties().get(PropertyName.fromElement(caldavxml.SupportedCalendarComponentSet)) is None)

        # Resource type removed
        self.assertTrue(cal.properties().get(PropertyName.fromElement(element.ResourceType)) is None)

        # Ctag removed
        self.assertTrue(cal.properties().get(PropertyName.fromElement(customxml.GETCTag)) is None)

        # Availability
        self.assertEquals(str(home.getAvailability()), str(self.av1))
        self.assertTrue(inbox.properties().get(PropertyName.fromElement(customxml.CalendarAvailability)) is None)

        # Default calendar
        self.assertTrue(home.isDefaultCalendar(cal))
        self.assertTrue(inbox.properties().get(PropertyName.fromElement(caldavxml.ScheduleDefaultCalendarURL)) is None)


    def test_fileStoreFromPath(self):
        """
        Verify that fileStoreFromPath() will return a CommonDataStore if
        the given path contains either "calendars" or "addressbooks"
        sub-directories.  Otherwise it returns None
        """

        # No child directories
        docRootPath = CachingFilePath(self.mktemp())
        docRootPath.createDirectory()
        step = UpgradeToDatabaseStep.fileStoreFromPath(docRootPath)
        self.assertEquals(step, None)

        # "calendars" child directory exists
        childPath = docRootPath.child("calendars")
        childPath.createDirectory()
        step = UpgradeToDatabaseStep.fileStoreFromPath(docRootPath)
        self.assertTrue(isinstance(step, CommonDataStore))
        childPath.remove()

        # "addressbooks" child directory exists
        childPath = docRootPath.child("addressbooks")
        childPath.createDirectory()
        step = UpgradeToDatabaseStep.fileStoreFromPath(docRootPath)
        self.assertTrue(isinstance(step, CommonDataStore))
        childPath.remove()
