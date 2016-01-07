##
# Copyright (c) 2005-2016 Apple Inc. All rights reserved.
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
from __future__ import with_statement

import os

from calendarserver.provision.root import RootResource
from calendarserver.tap.util import getRootResource
from twext.python.filepath import CachingFilePath as FilePath
from twext.python.log import Logger
from twisted.internet.base import DelayedCall
from twisted.internet.defer import succeed, fail, inlineCallbacks, returnValue
from twisted.internet.protocol import ProcessProtocol
from twisted.python.failure import Failure
from twistedcaldav import memcacher
from twistedcaldav.bind import doBind
from twistedcaldav.directory.addressbook import DirectoryAddressBookHomeProvisioningResource
from twistedcaldav.directory.calendar import DirectoryCalendarHomeProvisioningResource
from twistedcaldav.directory.util import transactionFromRequest
from twistedcaldav.memcacheclient import ClientFactory
from twistedcaldav.stdconfig import config
from txdav.common.datastore.file import CommonDataStore
from txdav.common.datastore.test.util import deriveQuota, CommonCommonTests
from txweb2.dav.test.util import SimpleRequest
from txweb2.http import HTTPError, StatusResponse
from txweb2.server import Site
import txweb2.dav.test.util
import xattr


log = Logger()


__all__ = [
    "featureUnimplemented",
    "testUnimplemented",
    "todo",
    "TestCase",
]
DelayedCall.debug = True


def _todo(f, why):
    f.todo = why
    return f


featureUnimplemented = lambda f: _todo(f, "Feature unimplemented")
testUnimplemented = lambda f: _todo(f, "Test unimplemented")
todo = lambda why: lambda f: _todo(f, why)

dirTest = FilePath(__file__).parent().sibling("directory").child("test")

xmlFile = dirTest.child("accounts.xml")
resourcesFile = dirTest.child("resources.xml")
augmentsFile = dirTest.child("augments.xml")
proxiesFile = dirTest.child("proxies.xml")



class SimpleStoreRequest(SimpleRequest):
    """
    A SimpleRequest that automatically grabs the proper transaction for a test.
    """
    def __init__(self, test, method, uri, headers=None, content=None, authPrincipal=None):
        super(SimpleStoreRequest, self).__init__(test.site, method, uri, headers, content)
        self._test = test
        self._newStoreTransaction = test.transactionUnderTest(txn=transactionFromRequest(self, test.storeUnderTest()))
        self.credentialFactories = {}

        # Fake credentials if auth needed
        if authPrincipal is not None:
            self.authzUser = self.authnUser = authPrincipal


    @inlineCallbacks
    def process(self):
        """
        Process will commit the transaction in the test so we need to clear it out.
        """
        result = yield super(SimpleStoreRequest, self).process()
        self._test.lastTransaction = None
        returnValue(result)


    def _cbFinishRender(self, result):
        self._test.lastTransaction = None
        return super(SimpleStoreRequest, self)._cbFinishRender(result)



class StoreTestCase(CommonCommonTests, txweb2.dav.test.util.TestCase):
    """
    A base class for tests that use the SQL store and need to create and
    verify filesystem hierarchies.
    """

    @inlineCallbacks
    def setUp(self):
        yield super(StoreTestCase, self).setUp()

        yield self.buildStoreAndDirectory()

        self.rootResource = getRootResource(config, self.store)
        self.actualRoot = self.rootResource.resource.resource
        self.site = Site(self.actualRoot)

        yield self.populate()


    def populate(self):
        return succeed(None)


    def configure(self):
        """
        Adjust the global configuration for this test.
        """
        super(StoreTestCase, self).configure()

        config.Memcached.Pools.Default.ClientEnabled = False
        config.Memcached.Pools.Default.ServerEnabled = False
        ClientFactory.allowTestCache = True
        memcacher.Memcacher.allowTestCache = True
        memcacher.Memcacher.reset()
        config.DirectoryAddressBook.Enabled = False
        config.UsePackageTimezones = True


    def createHierarchy(self, structure, root=None):
        if root is None:
            root = os.path.abspath(self.mktemp())
            os.mkdir(root)

        def createChildren(parent, subStructure):
            for childName, childStructure in subStructure.iteritems():

                if childName.startswith("@"):
                    continue

                childPath = os.path.join(parent, childName)
                if "@contents" in childStructure:
                    # This is a file
                    with open(childPath, "w") as child:
                        child.write(childStructure["@contents"])
                else:
                    # This is a directory
                    os.mkdir(childPath)
                    createChildren(childPath, childStructure)

                if "@xattrs" in childStructure:
                    xattrs = childStructure["@xattrs"]
                    for attr, value in xattrs.iteritems():
                        try:
                            xattr.setxattr(childPath, attr, value)
                        except IOError:
                            pass

                # Set access and modified times
                if "@timestamp" in childStructure:
                    timestamp = childStructure["@timestamp"]
                    os.utime(childPath, (timestamp, timestamp))

        createChildren(root, structure)
        return root


    def verifyHierarchy(self, root, structure):

        def verifyChildren(parent, subStructure):

            actual = set([child for child in os.listdir(parent)])

            for childName, childStructure in subStructure.iteritems():

                if childName.startswith("@"):
                    continue

                if childName in actual:
                    actual.remove(childName)

                if childName.startswith("*"):
                    if "/" in childName:
                        childName, matching = childName.split("/")
                    else:
                        matching = False
                    ext = childName.split(".")[1]
                    found = False
                    for actualFile in actual:
                        if actualFile.endswith(ext):
                            matches = True
                            if matching:
                                matches = False
                                # We want to target only the wildcard file containing
                                # the matching string
                                actualPath = os.path.join(parent, actualFile)
                                with open(actualPath) as child:
                                    contents = child.read()
                                    if matching in contents:
                                        matches = True

                            if matches:
                                actual.remove(actualFile)
                                found = True
                                break
                    if found:
                        # continue
                        childName = actualFile

                childPath = os.path.join(parent, childName)

                if not os.path.exists(childPath):
                    if "@optional" in childStructure:
                        return True
                    else:
                        print("Missing:", childPath)
                        return False

                if "@contents" in childStructure:
                    # This is a file
                    expectedContents = childStructure["@contents"]
                    if expectedContents is None:
                        # We don't care about the contents
                        pass
                    elif isinstance(expectedContents, tuple):
                        with open(childPath) as child:
                            contents = child.read()
                            for term in expectedContents:
                                if term not in contents:
                                    print("Contents mismatch:", childPath)
                                    print("Expecting match:\n%s\n\nActual:\n%s\n" % (term, contents))
                                    return False
                    else:
                        with open(childPath) as child:
                            contents = child.read()
                            if contents != childStructure["@contents"]:
                                print("Contents mismatch:", childPath)
                                print("Expected:\n%s\n\nActual:\n%s\n" % (childStructure["@contents"], contents))
                                return False

                else:
                    # This is a directory
                    if not verifyChildren(childPath, childStructure):
                        return False

                if "@xattrs" in childStructure:
                    try:
                        # See if we have xattr support; IOError if not
                        try:
                            xattr.getxattr(childPath, "test")
                        except KeyError:
                            pass

                        xattrs = childStructure["@xattrs"]
                        for attr, value in xattrs.iteritems():
                            if isinstance(value, str):
                                if xattr.getxattr(childPath, attr) != value:
                                    print("Xattr mismatch:", childPath, attr)
                                    print((xattr.getxattr(childPath, attr), " != ", value))
                                    return False
                            else:  # method
                                if not value(xattr.getxattr(childPath, attr)):
                                    return False

                        for attr, value in xattr.xattr(childPath).iteritems():
                            if attr not in xattrs:
                                return False
                    except IOError:
                        # xattr not enabled/supported
                        pass

            if actual:
                # There are unexpected children
                print("Unexpected:", actual, 'in', parent)
                return False

            return True

        return verifyChildren(root, structure)



class TestCase(txweb2.dav.test.util.TestCase):
    resource_class = RootResource

    def createDataStore(self):
        """
        Create an L{IDataStore} that can store calendars (but not
        addressbooks.)  By default returns a L{CommonDataStore}, but this is a
        hook for subclasses to override to provide different data stores.
        """
        return CommonDataStore(FilePath(config.DocumentRoot), None, None, True, False,
                               quota=deriveQuota(self))


    def setupCalendars(self):
        """
        When a directory service exists, set up the resources at C{/calendars}
        and C{/addressbooks} (a L{DirectoryCalendarHomeProvisioningResource}
        and L{DirectoryAddressBookHomeProvisioningResource} respectively), and
        assign them to the C{self.calendarCollection} and
        C{self.addressbookCollection} attributes.

        A directory service may be associated with this L{TestCase} with
        L{TestCase.createStockDirectoryService} or
        L{TestCase.directoryFixture.addDirectoryService}.
        """
        newStore = self.createDataStore()


        @self.directoryFixture.whenDirectoryServiceChanges
        def putAllChildren(ds):
            self.calendarCollection = (
                DirectoryCalendarHomeProvisioningResource(
                    ds, "/calendars/", newStore
                ))
            self.site.resource.putChild("calendars", self.calendarCollection)
            self.addressbookCollection = (
                DirectoryAddressBookHomeProvisioningResource(
                    ds, "/addressbooks/", newStore
                ))
            self.site.resource.putChild("addressbooks",
                                        self.addressbookCollection)


    def configure(self):
        """
        Adjust the global configuration for this test.
        """
        config.reset()

        config.ServerRoot = os.path.abspath(self.serverRoot)
        config.ConfigRoot = "config"
        config.LogRoot = "logs"
        config.RunRoot = "logs"

        config.Memcached.Pools.Default.ClientEnabled = False
        config.Memcached.Pools.Default.ServerEnabled = False
        ClientFactory.allowTestCache = True
        memcacher.Memcacher.allowTestCache = True
        memcacher.Memcacher.reset()
        config.DirectoryAddressBook.Enabled = False
        config.UsePackageTimezones = True


    def setUp(self):
        super(TestCase, self).setUp()

        # FIXME: this is only here to workaround circular imports
        doBind()

        self.serverRoot = self.mktemp()
        os.mkdir(self.serverRoot)

        self.configure()

        if not os.path.exists(config.DataRoot):
            os.makedirs(config.DataRoot)
        if not os.path.exists(config.DocumentRoot):
            os.makedirs(config.DocumentRoot)
        if not os.path.exists(config.ConfigRoot):
            os.makedirs(config.ConfigRoot)
        if not os.path.exists(config.LogRoot):
            os.makedirs(config.LogRoot)



class norequest(object):
    def addResponseFilter(self, filter):
        "stub; ignore me"



class InMemoryPropertyStore(object):
    def __init__(self):
        class _FauxPath(object):
            path = ':memory:'

        class _FauxResource(object):
            fp = _FauxPath()

        self._properties = {}
        self.resource = _FauxResource()


    def get(self, qname, uid=None):
        qnameuid = qname + (uid,)
        data = self._properties.get(qnameuid)
        if data is None:
            raise HTTPError(StatusResponse(404, "No such property"))
        return data


    def set(self, property, uid=None):
        qnameuid = property.qname() + (uid,)
        self._properties[qnameuid] = property


    def delete(self, qname, uid=None):
        try:
            qnameuid = qname + (uid,)
            del self._properties[qnameuid]
        except KeyError:
            pass


    def contains(self, qname, uid=None):
        qnameuid = qname + (uid,)
        return qnameuid in self._properties


    def list(self, uid=None, filterByUID=True):
        results = self._properties.iterkeys()
        if filterByUID:
            return [
                (namespace, name)
                for namespace, name, propuid in results
                if propuid == uid
            ]
        else:
            return results



class StubCacheChangeNotifier(object):
    def __init__(self, *args, **kwargs):
        pass

    changedCount = 0

    def changed(self):
        self.changedCount += 1
        return succeed(True)



class InMemoryMemcacheProtocol(object):
    def __init__(self, reactor=None):
        self._cache = {}

        if reactor is None:
            from twisted.internet import reactor

        self._reactor = reactor

        self._timeouts = {}


    def get(self, key):
        if key not in self._cache:
            return succeed((0, None))

        return succeed(self._cache[key])


    def _timeoutKey(self, expireTime, key):
        def _removeKey():
            del self._cache[key]

        if expireTime > 0:
            if key in self._timeouts:
                self._timeouts[key].cancel()

            self._timeouts[key] = self._reactor.callLater(
                expireTime,
                _removeKey)


    def set(self, key, value, flags=0, expireTime=0):
        try:
            self._cache[key] = (flags, value)

            self._timeoutKey(expireTime, key)

            return succeed(True)

        except Exception:
            return fail(Failure())


    def add(self, key, value, flags=0, expireTime=0):
        if key in self._cache:
            return succeed(False)

        return self.set(key, value, flags=flags, expireTime=expireTime)


    def delete(self, key):
        try:
            del self._cache[key]
            if key in self._timeouts:
                self._timeouts[key].cancel()
            return succeed(True)

        except:
            return succeed(False)



class ErrorOutput(Exception):
    """
    The process produced some error output and exited with a non-zero exit
    code.
    """



class CapturingProcessProtocol(ProcessProtocol):
    """
    A L{ProcessProtocol} that captures its output and error.

    @ivar output: a C{list} of all C{str}s received to stderr.

    @ivar error: a C{list} of all C{str}s received to stderr.
    """

    def __init__(self, deferred, inputData):
        """
        Initialize a L{CapturingProcessProtocol}.

        @param deferred: the L{Deferred} to fire when the process is complete.

        @param inputData: a C{str} to feed to the subprocess's stdin.
        """
        self.deferred = deferred
        self.input = inputData
        self.output = []
        self.error = []
        self.terminated = False


    def connectionMade(self):
        """
        The process started; feed its input on stdin.
        """
        if self.input is not None:
            self.transport.write(self.input)
            self.transport.closeStdin()


    def outReceived(self, data):
        """
        Some output was received on stdout.
        """
        self.output.append(data)


    def errReceived(self, data):
        """
        Some output was received on stderr.
        """
        # Ignore the Postgres "NOTICE" output
        if not data or "NOTICE" in data or "PowmInsecureWarning" in data:
            return

        self.error.append(data)

        # Attempt to exit promptly if a traceback is displayed, so we don't
        # deal with timeouts.
        if "Traceback" in data and not self.terminated:
            log.error("Terminating process due to output: {d}", d=data)
            self.terminated = True
            self.transport.signalProcess("TERM")


    def processEnded(self, why):
        """
        The process is over, fire the Deferred with the output.
        """
        if why.value.exitCode == 0 and not self.error:
            self.deferred.callback(''.join(self.output))
        else:
            errtxt = "Code: {}, Errors: {}".format(why.value.exitCode, repr(''.join(self.error)))
            self.deferred.errback(ErrorOutput(errtxt))
